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
  OWNER_PROGRESS_STEPS,
  reviewChecklistFor,
  reviewAutoFlags,
  reviewBlockReason,
  INSTALL_POLICY_LABELS,
  DISTRIBUTION_LABELS,
  RISK_DEFAULT_LABELS,
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
  mcpTrialMode,
  assetUrl,
  trialLabel
} from '../utils/format'
import { toast } from '../utils/toast'
import StatusBadge from '../components/StatusBadge.vue'
import PackagePreview from '../components/PackagePreview.vue'
import PackageEditor from '../components/PackageEditor.vue'
import DebugCapabilityModal from '../components/DebugCapabilityModal.vue'
import ConfirmActionModal from '../components/ConfirmActionModal.vue'
import AskTrialPanel from '../components/AskTrialPanel.vue'
import MultiSelect from '../components/MultiSelect.vue'
import WorkflowChat from '../components/WorkflowChat.vue'

const __API_BASE__ = (import.meta.env.BASE_URL || '/').replace(/\/$/, '') + '/api'

const props = defineProps({ id: { type: String, required: true } })
const router = useRouter()
const route = useRoute()

function goBack() {
  const back = window.history.state?.back
  if (typeof back === 'string' && back.length) {
    router.back()
    return
  }
  const type = cap.value?.type
  if (type === 'plugin') {
    router.push({ path: '/', query: { shelf: 'install' } })
    return
  }
  if (type) {
    router.push({ path: '/', query: { type } })
    return
  }
  router.push('/')
}

const cap = ref(null)
const loading = ref(true)
const versions = ref([])
const ratings = ref([])
const ratingsExpanded = ref(false)
const ratingBusy = ref(false)
const myBusy = ref(false)
const error = ref('')
const notice = ref('')
const reviewComment = ref('')
const rating = ref({ score: 5, comment: '' })
const ratingHover = ref(0)
const ratingPop = ref(0)

function setRating(score) {
  rating.value.score = score
  ratingPop.value = 0
  requestAnimationFrame(() => {
    ratingPop.value = score
  })
}
const visibleRatings = computed(() =>
  ratingsExpanded.value ? ratings.value : ratings.value.slice(0, 3)
)
const newVersion = ref('')
const versionChangelog = ref('')
const versionSuggestions = ref({ current: '', major: '', minor: '', patch: '' })
const uploading = ref(false)
const saving = ref(false)
const myIds = ref(new Set())
const myNotice = ref('')
const confirmState = ref(null)
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
const allowedUsers = ref([])
const allowedDepartments = ref([])
const allowedRoles = ref([])
const accessSaved = ref('')
const ACCESS_ROLE_KEYS = ['admin', 'user']

// 白名单下拉选项(用户/部门): 懒加载; 失败时下拉为空, 仍可手输新增
const accessOptions = ref({ users: [], departments: [] })
let accessOptionsLoaded = false
const userOptions = computed(() =>
  (accessOptions.value.users || []).map((u) => ({
    value: u.username,
    label: u.name ? `${u.name}（${u.username}）` : u.username
  }))
)
const accessDepartmentOptions = computed(() => accessOptions.value.departments || [])
async function loadAccessOptions() {
  if (accessOptionsLoaded) return
  try {
    accessOptions.value = await api.get('/meta/access-options')
    accessOptionsLoaded = true
  } catch (e) {
    console.warn('访问权限选项加载失败，下拉为空仍可手输', e)
  }
}

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
const authorDecision = ref(null)
const allReviewChecked = computed(() => reviewChecks.value.length > 0 && reviewChecks.value.every(Boolean))
const approveBlock = computed(() => reviewBlockReason(cap.value))
const replacesVersion = computed(() => {
  const pubs = (versions.value || []).filter((v) => v.status === 'published' && v.id !== cap.value?.id)
  if (!pubs.length) return ''
  const best = pubs.reduce((a, b) => {
    const pa = String(a.version).split('.').map(Number)
    const pb = String(b.version).split('.').map(Number)
    for (let i = 0; i < 3; i++) {
      if ((pa[i] || 0) !== (pb[i] || 0)) return (pa[i] || 0) > (pb[i] || 0) ? a : b
    }
    return a
  })
  return best.version || ''
})
const needsPackage = computed(() => cap.value && needsZipUpload(cap.value.type))
const showPackageUpload = computed(
  () =>
    needsPackage.value &&
    isOwner.value &&
    ['draft', 'returned', 'rejected'].includes(cap.value?.status)
)

const reviewAuto = ref([])
const activeChecks = computed(() => reviewChecklistFor(cap.value))
const pendingCheckLabels = computed(() =>
  activeChecks.value.filter((_, i) => !reviewChecks.value[i]).map((item) => item.short)
)

function applyReviewChecks() {
  const items = reviewChecklistFor(cap.value)
  const flags = reviewAutoFlags(cap.value, items)
  reviewAuto.value = flags
  reviewChecks.value = flags.slice()
}

const platformSecretsReady = computed(() => {
  if (!isMcp.value || !(isOwner.value || isAdmin.value) || !platformLoaded.value) return null
  const required = platformSecretRows.value.filter((row) => row.secret)
  if (!required.length) return true
  return required.every((row) => row.row)
})

function distributionPlain(value) {
  if (value === 'remote') return '云端使用，不用安装'
  if (value === 'local') return '装到本机后使用'
  if (value === 'both') return '云端或本机都可以'
  return value || '—'
}

const consumerHint = computed(() => {
  const dist = cap.value?.distribution
  if (!joined.value) {
    if (dist === 'remote') return '「加入」是授权你使用。加入后在对话里直接用，不用安装。'
    if (dist === 'local') return '「加入」是把它放进你的能力。之后可以试用，也可以装到零号员工。'
    return '「加入」是授权你使用。云端可以直接用，也可以装到本机。'
  }
  if (dist === 'remote') return '已加入，在对话里直接用。'
  if (dist === 'local') return '已加入。要在本机用，打开下面的「其他方式」。'
  return '已加入。云端可以直接用；要装到本机，打开下面的「其他方式」。'
})

const ownerNextHint = computed(() => {
  if (cap.value?.status === 'reviewing') return '已提交，等待其他管理员审核。审核期间不能改文件，要改请先撤回。'
  if (cap.value?.status === 'returned') return '已打回。按意见修改后可以重新提交。'
  if (cap.value?.status === 'rejected') return '已驳回。按意见修改后可以重新提交。'
  if (needsPackageFirst.value) return '先完善内容并保存，再提交审核。'
  if (canSubmit.value) return '内容已齐，可以提交审核。'
  return '完善内容后提交审核，上架后才能加入。'
})

const AUTHOR_TAB_KEYS = ['files', 'manage']
const readerTabs = computed(() => contentTabs.value.filter((tab) => !AUTHOR_TAB_KEYS.includes(tab.key)))
const authorTabs = computed(() => contentTabs.value.filter((tab) => AUTHOR_TAB_KEYS.includes(tab.key)))

function applyRouteTab() {
  const keys = contentTabs.value.map((item) => item.key)
  if (!keys.length) return
  if (route.query.focus === 'package' && keys.includes('files') && !route.query.tab) {
    contentTab.value = 'files'
    return
  }
  const tab = typeof route.query.tab === 'string' ? route.query.tab : ''
  if (tab && keys.includes(tab)) contentTab.value = tab
  else if (!keys.includes(contentTab.value)) contentTab.value = 'intro'
}

function setContentTab(key) {
  contentTab.value = key
  const query = { ...route.query }
  if (!key || key === 'intro') delete query.tab
  else query.tab = key
  if (String(route.query.tab || '') === String(query.tab || '')) return
  router.replace({ query })
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

const canEdit = computed(
  () => (isOwner.value || isAdmin.value) && ['draft', 'returned', 'rejected'].includes(cap.value?.status)
)
/** 可在「文件预览」Tab 直接编辑/上传包内文件：作者或管理员，且处于可上传状态 */
const canEditPackage = computed(
  () =>
    (isOwner.value || isAdmin.value) &&
    ['draft', 'returned', 'rejected'].includes(cap.value?.status)
)
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
const canReview = computed(
  () => isAdmin.value && !isOwner.value && cap.value?.status === 'reviewing'
)
/** 可查看/管理能力包与产物（作者/管理员）：文件预览等作者面仅对其可见 */
const canViewPackage = computed(
  () => isOwner.value || isAdmin.value || canEdit.value || canReview.value
)
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
const progressSteps = computed(() =>
  OWNER_PROGRESS_STEPS.filter((step) => ['created', 'package', 'submit', 'reviewing', 'published'].includes(step.key))
)
const progressDone = computed(() => ['published', 'deprecated'].includes(cap.value?.status))
const progressIndex = computed(() => {
  const steps = progressSteps.value
  if (progressDone.value) return Math.max(0, steps.length - 1)
  const raw = ownerProgressIndex(cap.value, { joined: false })
  const key = OWNER_PROGRESS_STEPS[raw]?.key
  const idx = steps.findIndex((step) => step.key === key)
  return idx >= 0 ? idx : 0
})
const onlineEditPath = computed(() => (cap.value ? editRouteFor(cap.value) : null))
const preferOnlineEdit = computed(
  () => Boolean(onlineEditPath.value && (isOwner.value || isAdmin.value) && canOnlineEdit(cap.value?.type))
)
/** 已发布/已下架：属主或管理员可一键开新版（继承能力包）后在线编辑 */
const canRevise = computed(
  () =>
    (isOwner.value || isAdmin.value) &&
    ['published', 'deprecated'].includes(cap.value?.status)
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
function versionNewer(a, b) {
  const parts = (v) => String(v || '0').split(/[.+-]/).map((n) => parseInt(n, 10) || 0)
  const pa = parts(a)
  const pb = parts(b)
  const n = Math.max(pa.length, pb.length)
  for (let i = 0; i < n; i += 1) {
    const d = (pa[i] || 0) - (pb[i] || 0)
    if (d) return d > 0
  }
  return false
}
const usedByRawCount = computed(() => (Array.isArray(cap.value?.used_by) ? cap.value.used_by.length : 0))
const usedBy = computed(() => {
  const rows = Array.isArray(cap.value?.used_by) ? cap.value.used_by : []
  const latest = new Map()
  for (const u of rows) {
    const key = `${u.type || ''}\0${String(u.name || '').trim().toLowerCase()}`
    const cur = latest.get(key)
    if (!cur || versionNewer(u.version, cur.version)) latest.set(key, u)
  }
  return [...latest.values()].sort((a, b) =>
    String(a.type || '').localeCompare(String(b.type || ''))
    || String(a.name || '').localeCompare(String(b.name || ''), 'zh')
  )
})
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
const showPrimaryUsage = computed(() => {
  if (showAskTrial.value && askAgentName.value) return true
  return Boolean(isMcp.value && isPublished.value && !usedByAgents.value.length)
})
const showUsageMore = computed(() => {
  if (!cap.value) return false
  if (isMcp.value && isPublished.value && usedByAgents.value.length) return true
  if (isMcp.value && mcpClientConfigJson.value) return true
  if ((cap.value.artifacts || []).length && canViewPackage.value) return true
  return Boolean(isOwner.value || isAdmin.value)
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
  if (!authState.token) return '用企业统一登录后才能试用。'
  if (askCanRun.value) return ''
  return '先「加入」授权，再试用。'
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
const hasExtraWays = computed(() => {
  if (!cap.value || !isPublished.value) return false
  if (joined.value && (cap.value.install_policy || 'optional') !== 'required') return true
  if (canLocalInstall.value && installCommand.value) return true
  if (!isRemoteOnly.value) return true
  return Boolean(authState.token)
})
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
const contentTab = ref('intro')
// ── 工作流"作为应用"运行 ──
const runMeta = ref(null)
const runForm = reactive({})
const runBusy = ref(false)
const runError = ref('')
const runResult = ref('')
const runExecutions = ref([])
const runLoading = ref(false)
const isRunnableApp = computed(() => isWorkflow.value && isPublished.value)

async function loadRunMeta() {
  if (!isRunnableApp.value) {
    runMeta.value = null
    return
  }
  runLoading.value = true
  runError.value = ''
  try {
    const meta = await api.get(`/runtime/workflows/${cap.value.name}/run-meta`)
    runMeta.value = meta
    for (const k of Object.keys(runForm)) delete runForm[k]
    for (const f of meta.input_fields || []) {
      runForm[f.key] = f.default !== undefined && f.default !== null ? f.default : ''
    }
    if (meta.shape !== 'chat') await loadExecutions()
  } catch (e) {
    runError.value = e?.message || '加载运行信息失败'
  } finally {
    runLoading.value = false
  }
}

async function loadExecutions() {
  if (!cap.value) return
  try {
    runExecutions.value = await api.get(`/runtime/workflows/${cap.value.name}/executions`)
  } catch {
    runExecutions.value = []
  }
}

async function submitRun() {
  if (!cap.value) return
  runBusy.value = true
  runError.value = ''
  runResult.value = ''
  try {
    const ex = await api.post(`/runtime/workflows/${cap.value.name}/run`, { input: { ...runForm } })
    const outs = ex.outputs || {}
    const endOut = outs.end || outs
    runResult.value = JSON.stringify(endOut, null, 2)
    if (ex.state === 'failed') runError.value = ex.error || '运行失败'
    await loadExecutions()
  } catch (e) {
    runError.value = e?.message || '运行失败'
  } finally {
    runBusy.value = false
  }
}
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

async function clearPlatformSecret(row, confirmed = false) {
  const capId = cap.value?.id
  if (!row?.key || !capId) return
  if (!confirmed) {
    confirmState.value = {
      kind: 'clear-secret',
      row,
      title: `清除平台密钥「${row.key}」？`,
      body: '清除后平台轨将无法注入该变量。',
      okText: '清除',
      danger: true
    }
    return
  }
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
      return { kind: 'trial-agent', label: `试用 · ${usedByAgents.value[0].name}` }
    }
    if (canTrialCurrent.value) return { kind: 'trial', label: trialLabel(isAgent.value ? 'agent' : cap.value?.type) }
    return { kind: 'mine', label: '去我的能力' }
  }
  if (!joined.value) {
    return { kind: 'join', label: isPlugin.value ? '加入 · 能力包' : '加入' }
  }
  return trialLike()
})
const installTitle = computed(() => {
  const kind = nextStep.value?.kind
  if (kind === 'join') return '加入我的能力'
  if (kind === 'login') return '登录后加入'
  if (kind === 'trial-agent') return `试用 · ${usedByAgents.value[0]?.name || '专家'}`
  if (kind === 'trial') return trialLabel(isAgent.value ? 'agent' : cap.value?.type)
  if (kind === 'mine') return '已加入'
  return '使用这个能力'
})
const joinedHint = computed(() => {
  if (cap.value?.distribution === 'remote') return '已加入，在对话里直接用。'
  if (cap.value?.distribution === 'local') return '已加入，可以试用，也可以装到零号员工。'
  return '已加入。云端可以直接用，也可以装到本机。'
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
  if (isRunnableApp.value) tabs.push({ key: 'run', label: '使用' })
  if (isMcp.value && mcpToolRows.value.length) {
    tabs.push({ key: 'tools', label: `工具（${mcpToolRows.value.length}）` })
  }
  if (isPlugin.value) tabs.push({ key: 'components', label: '组件' })
  if (isAgent.value && (embeddedSkills.value.length || embeddedMcp.value.length)) {
    tabs.push({ key: 'bundle', label: '内含能力' })
  }
  if (isMcp.value) {
    tabs.push({ key: 'config', label: '配置' })
  }
  tabs.push({ key: 'versions', label: '版本' })
  if (((cap.value.artifacts || []).length && canViewPackage.value) || canEditPackage.value) {
    tabs.push({ key: 'files', label: '文件' })
  }
  if (canEdit.value || canReview.value || isOwner.value || isAdmin.value) {
    tabs.push({ key: 'manage', label: '管理' })
  }
  return tabs
})
const typeInitial = computed(() => {
  // 默认头像用「名称/显示名」首字符（不是类型）
  const n = (displayName.value || '').trim()
  return n ? n.slice(0, 1).toUpperCase() : '?'
})
const readmeHtml = computed(() => {
  let md = (cap.value?.readme_md || '').trim()
  if (!md) return ''
  const ver = (cap.value?.version || '').trim()
  if (ver) {
    md = md.replace(/(cap\s+install\s+\S+@)\d+(?:\.\d+)*/gi, `$1${ver}`)
  }
  try {
    return marked.parse(md, { gfm: true, breaks: true })
  } catch {
    return ''
  }
})
const introTags = computed(() =>
  (cap.value?.tags || []).filter((t) => t && t !== 'plugin-component').slice(0, 8)
)
const fallbackToolPreview = computed(() => mcpToolRows.value.slice(0, 5))

watch(cap, () => {
  iconFailed.value = false
  applyReviewChecks()
  applyRouteTab()
  loadRunMeta()
})

watch(
  () => route.query.tab,
  () => applyRouteTab()
)

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
    setContentTab('intro')
    nextTick(() => askTrialRef.value?.focus?.())
    return
  }
  debugCap.value = stubFromCap(cap.value)
}

function openTrialAgent(u) {
  if (showAskTrial.value && (u?.name || askAgentName.value)) {
    setContentTab('intro')
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
    allowedUsers.value = normList(cap.value.allowed_users)
    allowedDepartments.value = normList(cap.value.allowed_departments)
    allowedRoles.value = normList(cap.value.allowed_roles)
    if (authState.token && (isOwner.value || isAdmin.value)) {
      void loadAccessOptions()
    }
    versions.value = await api.get(`/capabilities/${props.id}/versions`)
    ratings.value = await api.get(`/capabilities/${props.id}/ratings`)
    await loadAuthorDecision()
    await loadMcpPackageMeta()
    await loadPlatformSecrets()
    error.value = ''
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function loadAuthorDecision() {
  authorDecision.value = null
  if (!authState.token || !isOwner.value) return
  if (!['returned', 'rejected'].includes(cap.value?.status)) return
  try {
    const rows = await api.get(`/admin/capabilities/${props.id}/reviews`)
    authorDecision.value = (rows || []).find((row) => row.action === 'reject' || row.action === 'return') || null
  } catch {
    authorDecision.value = null
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
  if (myBusy.value) return
  if (myIds.value.has(props.id)) {
    if ((cap.value?.install_policy || 'optional') === 'required') {
      myNotice.value = '必装能力不可移除'
      return
    }
    confirmState.value = {
      kind: 'remove-mine',
      title: '确定移除？',
      body: `移除后，「${cap.value?.display_name || cap.value?.name || ''}」将不再出现在自定义列表中，不影响目录上架状态。`,
      okText: '移除',
      danger: false
    }
    return
  }
  if (!subscribeGate.value.ok) {
    myNotice.value = subscribeGate.value.reason
    return
  }
  myBusy.value = true
  try {
    const r = await api.post('/my/capabilities', { capability_id: props.id })
    myIds.value = new Set([...myIds.value, props.id])
    try {
      const mine = await api.get('/my/capabilities?scope=added')
      myIds.value = new Set((mine || []).filter((c) => c.added).map((c) => c.id))
    } catch {
      /* ignore refresh errors */
    }
    const extra = r?.message && r.message.includes('未加入') ? `；${r.message}` : ''
    toast.success(joinedHint.value + extra)
  } catch (e) {
    toast.error(e.message)
  } finally {
    myBusy.value = false
  }
}

function askReview(action) {
  if (!reviewComment.value.trim()) {
    error.value = '请先写下审核意见，作者会在详情页看到'
    return
  }
  const name = cap.value?.display_name || cap.value?.name || ''
  confirmState.value = {
    kind: 'review',
    action,
    title: action === 'reject' ? `拒绝「${name}」？` : `打回「${name}」？`,
    body:
      action === 'reject'
        ? '拒绝后状态为「已驳回」。作者按意见修改后可以重新提交。'
        : '打回后状态为「已打回」，不会变回草稿。作者按意见修改后可以重新提交。',
    okText: action === 'reject' ? '拒绝' : '打回',
    danger: action === 'reject'
  }
}

function askApprove() {
  if (approveBlock.value || !allReviewChecked.value) return
  const name = cap.value?.display_name || cap.value?.name || ''
  const live = replacesVersion.value
  confirmState.value = {
    kind: 'review',
    action: 'approve',
    title: `通过并上架「${name}」？`,
    body: live ? `通过后立即上架。已上架的 v${live} 会变为已弃用。` : '通过后立即上架。',
    okText: '通过并上架',
    danger: false
  }
}

function askStatus(action) {
  const name = cap.value?.display_name || cap.value?.name || ''
  confirmState.value = {
    kind: 'status',
    path: `/admin/capabilities/${props.id}/${action}`,
    title: action === 'deprecate' ? `下架「${name}」？` : `归档「${name}」？`,
    body:
      action === 'deprecate'
        ? '下架后，该能力不再作为可安装的上架能力，仍可在治理台的「已下架」中查看。'
        : '归档后，该能力会离开上架治理列表，本页不能撤销。',
    okText: action === 'deprecate' ? '下架' : '归档',
    danger: true
  }
}

async function onConfirmOk() {
  const state = confirmState.value
  confirmState.value = null
  if (!state) return
  if (state.kind === 'remove-mine') await removeMine()
  else if (state.kind === 'review') submitReview(state.action)
  else if (state.kind === 'status') doAction(state.path)
  else if (state.kind === 'delete-cap') await removeCap(true)
  else if (state.kind === 'delete-version') await deleteVersion(state.version, true)
  else if (state.kind === 'change-type') await saveMeta(true)
  else if (state.kind === 'clear-secret') await clearPlatformSecret(state.row, true)
  else if (state.kind === 'delete-icon') await removeIcon(true)
}

async function removeMine() {
  myNotice.value = ''
  myBusy.value = true
  try {
    const r = await api.delete(`/my/capabilities/${props.id}`)
    const next = new Set(myIds.value)
    next.delete(props.id)
    myIds.value = next
    toast.success(r.message || '已移除')
  } catch (e) {
    toast.error(e.message)
  } finally {
    myBusy.value = false
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

async function removeIcon(confirmed = false) {
  if (!cap.value?.icon_url) return
  if (!confirmed) {
    confirmState.value = {
      kind: 'delete-icon',
      title: '删除能力头像？',
      body: '删除后显示名称首字。',
      okText: '删除',
      danger: true
    }
    return
  }
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

async function saveAccess() {
  accessSaved.value = ''
  try {
    await api.post(`/capabilities/${props.id}/access`, {
      access_policy: accessPolicy.value,
      allowed_users: [...allowedUsers.value],
      // 未传=保持、传空数组=清空；本页始终显式提交两个名单
      allowed_departments: [...allowedDepartments.value],
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

const revising = ref(false)
const reviseError = ref('')

/** 一键基于已发布版本开新版（继承能力包），跳转到新草稿继续在线编辑/上传 */
async function openEditDraft() {
  if (revising.value) return
  revising.value = true
  reviseError.value = ''
  try {
    const draft = await api.post(`/publish/capabilities/${props.id}/edit-draft`, {})
    if (!draft?.id) throw new Error('已创建版本但未返回草稿 id')
    if (cap.value?.type === 'workflow') {
      // 工作流走专用编排器编辑
      await router.push(`/workflows/${draft.id}/edit`)
    } else {
      await router.push({
        path: `/capabilities/${draft.id}`,
        query: { focus: 'files', created: draft.version }
      })
    }
  } catch (e) {
    if (e && (e.name === 'NavigationDuplicated' || String(e.message || '').includes('Avoided redundant'))) {
      return
    }
    reviseError.value = e.message || String(e)
  } finally {
    revising.value = false
  }
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
      query: { focus: 'files', created: v.version }
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
  if (ratingBusy.value) return
  error.value = ''
  ratingBusy.value = true
  try {
    await api.post(`/capabilities/${props.id}/ratings`, rating.value)
    rating.value = { score: 5, comment: '' }
    toast.success('评分已提交')
    await load()
  } catch (e) {
    toast.error(e.message)
  } finally {
    ratingBusy.value = false
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
      toast.success('已取消订阅更新')
    } else {
      await api.post('/subscriptions', { capability_name: name })
      subscribedNames.value = new Set([...subscribedNames.value, name])
      toast.success('订阅成功，新版本发布时将收到通知')
    }
  } catch (e) {
    toast.error(e.message)
  } finally {
    subBusy.value = false
  }
}

function onEditTypeChange() {
  if (!(TYPE_CATEGORIES[editForm.type] || []).includes(editForm.category)) {
    editForm.category = ''
  }
}

async function saveMeta(confirmed = false) {
  error.value = ''
  notice.value = ''
  if (!editForm.name.trim()) {
    error.value = '请填写能力名称'
    return
  }
  if (!confirmed && editForm.type !== cap.value.type && (cap.value.artifacts || []).length) {
    confirmState.value = {
      kind: 'change-type',
      title: '更改类型？',
      body: '更改类型将清空已上传的能力包，需按新类型重新上传。',
      okText: '继续',
      danger: true
    }
    return
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

function askRemoveCap() {
  confirmState.value = {
    kind: 'delete-cap',
    title: `删除「${cap.value.name} v${cap.value.version}」？`,
    body: '删除后可使用该名称重新创建。',
    okText: '删除',
    danger: true
  }
}

async function removeCap(confirmed = false) {
  if (!confirmed) {
    askRemoveCap()
    return
  }
  error.value = ''
  try {
    await api.delete(`/publish/capabilities/${props.id}`)
    router.push('/my')
  } catch (e) {
    error.value = e.message
  }
}

function askDeleteVersion(v) {
  confirmState.value = {
    kind: 'delete-version',
    version: v,
    title: `删除版本 v${v.version}（${STATUS_LABELS[v.status] || v.status}）？`,
    body: '删除后可使用该版本号重新创建。',
    okText: '删除',
    danger: true
  }
}

async function deleteVersion(v, confirmed = false) {
  if (!confirmed) {
    askDeleteVersion(v)
    return
  }
  error.value = ''
  notice.value = ''
  try {
    await api.delete(`/publish/capabilities/${v.id}`)
    if (v.id === props.id) {
      router.push('/my')
      return
    }
    notice.value = `版本 v${v.version} 已删除`
    await load()
    await loadVersionSuggestions()
  } catch (e) {
    error.value = e.message
  }
}

function focusPackagePanel() {
  setContentTab('files')
}

/** 统一在线编辑入口：workflow 走可视化编排器；已发布→开新版；草稿→打开文件编辑器 */
function goEdit() {
  // 已发布（含工作流）→ 开新版草稿再编辑
  if (canRevise.value) {
    openEditDraft()
    return
  }
  if (cap.value?.type === 'workflow' && onlineEditPath.value) {
    router.push(onlineEditPath.value)
    return
  }
  focusPackagePanel()
}

async function bootstrapDetail({ keepNotice = false } = {}) {
  if (!cap.value) loading.value = true
  if (!keepNotice) notice.value = ''
  error.value = ''
  await load()
  await loadVersionSuggestions()
  loadMy()
  if (route.query.created) {
    notice.value = `新版本 v${route.query.created} 草稿已创建`
  }
  if (route.query.focus === 'files' || route.query.focus === 'package') {
    focusPackagePanel()
  }
  // 「在线编辑」入口带 edit=1：已发布能力自动开新版草稿后进入同一文件编辑器
  if (route.query.edit === '1' && !route.query.created && canRevise.value && !revising.value) {
    await openEditDraft()
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
  <div v-if="loading && !cap" class="detail">
    <div class="panel detail-hero">
      <div class="skel-row">
        <div class="skeleton skel-icon" style="width:56px;height:56px"></div>
        <div style="flex:1">
          <div class="skeleton skel-title"></div>
          <div class="skeleton skel-line w70"></div>
          <div class="skeleton skel-line w40"></div>
        </div>
      </div>
    </div>
  </div>
  <div v-else-if="error && !cap" class="empty empty-guide">
    <p>{{ error }}</p>
    <div class="flex" style="justify-content:center">
      <button class="btn btn-primary" type="button" @click="bootstrapDetail()">重试</button>
      <button class="btn" type="button" @click="goBack">返回</button>
    </div>
  </div>
  <div v-else-if="cap" class="detail">
    <div v-if="error" class="alert alert-error">{{ error }}</div>
    <div v-if="notice" class="alert alert-success">{{ notice }}</div>

    <nav class="detail-crumb muted">
      <button class="detail-back" type="button" title="返回" aria-label="返回" @click="goBack">
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
          <path
            fill="none"
            stroke="currentColor"
            stroke-width="1.75"
            stroke-linecap="round"
            stroke-linejoin="round"
            d="M15 5.5 8.5 12 15 18.5"
          />
        </svg>
      </button>
      <router-link to="/">发现</router-link>
      <span>/</span>
      <router-link v-if="cap.type === 'skill'" :to="{ path: '/', query: { type: 'skill' } }">技能</router-link>
      <router-link v-else-if="cap.type === 'agent'" :to="{ path: '/', query: { type: 'agent' } }">专家</router-link>
      <router-link v-else-if="cap.type === 'plugin'" :to="{ path: '/', query: { shelf: 'install' } }">能力包</router-link>
      <router-link v-else-if="shelfName" :to="{ path: '/', query: { type: cap.type } }">{{ TYPE_LABELS[cap.type] || shelfName }}</router-link>
      <span v-if="cap.type || shelfName">/</span>
      <span>{{ displayName }}</span>
    </nav>

    <div v-if="isOwner && authorDecision" class="alert mb-16">
      <strong>{{ authorDecision.action === 'reject' ? '已驳回' : '已打回' }}</strong>
      <div>{{ authorDecision.comment || '没有填写审核意见' }}</div>
    </div>

    <div v-if="isOwner && !progressDone" class="owner-progress panel mb-16">
      <div
        v-for="(step, i) in progressSteps"
        :key="step.key"
        class="owner-progress-step"
        :class="{ done: i < progressIndex || (progressDone && i === progressIndex), active: i === progressIndex && !progressDone }"
      >
        <span class="n">{{ i + 1 }}</span>
        <span class="l">{{ step.label }}</span>
      </div>
    </div>

    <section class="detail-hero">
      <div class="hero-top">
        <img
          v-if="cap.icon_url && !iconFailed"
          class="detail-hero-icon icon-img"
          :src="assetUrl(cap.icon_url)"
          :alt="cap.name"
          @error="iconFailed = true"
        />
        <div v-else class="detail-hero-icon" aria-hidden="true">{{ typeInitial }}</div>
        <div class="detail-hero-main">
          <h1 class="detail-title">{{ displayName }}</h1>
          <p v-if="cap.name && cap.name !== displayName" class="hero-slug">{{ cap.name }}</p>
          <p class="hero-source">
            <span>作者 {{ cap.author_name || '未知' }}</span>
            <span class="stat-dot">·</span>
            <span>{{ TYPE_LABELS[cap.type] }}</span>
            <template v-if="cap.verified">
              <span class="stat-dot">·</span>
              <span>已认证</span>
            </template>
          </p>
          <p class="detail-tagline">{{ cap.description || kindHint?.where || '暂无简介' }}</p>
          <div class="hero-facts">
            <span class="fact-pill">{{ STATUS_LABELS[cap.status] || cap.status }}</span>
            <span>{{ formatDate(cap.updated_at) }} 更新</span>
            <span class="stat-dot">·</span>
            <span>v{{ cap.version }}</span>
            <template v-if="cap.rating_count">
              <span class="stat-dot">·</span>
              <span class="stat-stars">{{ stars(cap.avg_rating) }}</span>
              <span>{{ formatStat(cap.rating_count) }} 评价</span>
            </template>
            <span class="stat-dot">·</span>
            <span>{{ formatStat(cap.usage_count) }} 次使用</span>
          </div>
        </div>
      </div>
    </section>

    <div class="detail-layout">
      <div class="detail-main">
        <div class="detail-tabs" role="tablist">
          <button
            v-for="t in readerTabs"
            :key="t.key"
            type="button"
            class="detail-tab"
            :class="{ active: contentTab === t.key }"
            role="tab"
            :aria-selected="contentTab === t.key"
            @click="setContentTab(t.key)"
          >
            {{ t.label }}
          </button>
          <span v-if="authorTabs.length" class="tab-split">维护</span>
          <button
            v-for="t in authorTabs"
            :key="t.key"
            type="button"
            class="detail-tab author-tab"
            :class="{ active: contentTab === t.key }"
            role="tab"
            :aria-selected="contentTab === t.key"
            @click="setContentTab(t.key)"
          >
            {{ t.label }}
          </button>
        </div>

        <section v-show="contentTab === 'run'" class="panel">
          <h2 class="detail-section-title">使用</h2>
          <div v-if="runLoading" class="muted">加载中…</div>
          <div v-else-if="runError && !runMeta" class="error">{{ runError }}</div>
          <template v-else-if="runMeta">
            <WorkflowChat v-if="runMeta.shape === 'chat'" :id="props.id" />
            <template v-else>
              <div v-if="runMeta.shape === 'automation' && runMeta.trigger && runMeta.trigger.type" class="guide-block">
                <h3 class="guide-title">触发方式</h3>
                <div class="muted" style="font-size: 12px">
                  类型：{{ runMeta.trigger.type }}
                  <span v-if="runMeta.trigger.cron">｜ cron：{{ runMeta.trigger.cron }}</span>
                </div>
              </div>
              <div v-for="f in runMeta.input_fields" :key="f.key" class="field">
                <label>
                  {{ f.label }}
                  <span v-if="f.required" style="color: var(--danger)">*</span>
                </label>
                <select v-if="f.type === 'select'" v-model="runForm[f.key]" class="select">
                  <option v-for="o in f.options" :key="o" :value="o">{{ o }}</option>
                </select>
                <textarea
                  v-else-if="f.type === 'textarea'"
                  v-model="runForm[f.key]"
                  class="textarea"
                  rows="3"
                  :placeholder="f.placeholder || ''"
                ></textarea>
                <input
                  v-else
                  v-model="runForm[f.key]"
                  class="input"
                  :type="f.type === 'number' ? 'number' : f.type === 'date' ? 'date' : 'text'"
                  :placeholder="f.placeholder || ''"
                />
              </div>
              <div v-if="!runMeta.input_fields.length" class="muted" style="font-size: 12px">
                该应用无输入参数。
              </div>
              <div class="flex mt-12">
                <button class="btn btn-primary" type="button" :disabled="runBusy" @click="submitRun">
                  {{ runBusy ? '运行中…' : runMeta.shape === 'automation' ? '手动运行' : '运行' }}
                </button>
                <button class="btn" type="button" @click="loadExecutions">刷新记录</button>
              </div>
              <div v-if="runError" class="error mt-12">{{ runError }}</div>
              <div v-if="runResult" class="guide-block mt-16">
                <h3 class="guide-title">结果</h3>
                <pre class="run-output">{{ runResult }}</pre>
              </div>
              <div class="guide-block mt-16">
                <h3 class="guide-title">最近运行</h3>
                <div v-for="ex in runExecutions" :key="ex.id" class="run-exec">
                  <span
                    class="badge"
                    :class="{
                      'badge-success': ex.state === 'succeeded',
                      'badge-danger': ['failed', 'canceled'].includes(ex.state),
                      'badge-warning': ['running', 'waiting'].includes(ex.state)
                    }"
                  >{{ ex.state }}</span>
                  <span class="muted" style="font-size: 12px">{{ formatDate(ex.updated_at) }}</span>
                </div>
                <div v-if="!runExecutions.length" class="muted" style="font-size: 12px">暂无运行记录</div>
              </div>
            </template>
          </template>
        </section>

        <div v-show="['intro', 'config'].includes(contentTab)" class="intro-stack">
          <section
            v-show="contentTab === 'intro'"
            class="panel overview"
          >
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
            <div v-if="isMcp && isPublished && !usedByAgents.length" class="guide-block trial-block">
              <h3 class="guide-title">{{ trialLabel('mcp') }}</h3>
              <p class="guide-lead">{{ mcpTrial.hint }}</p>
              <div class="trial-actions">
                <button v-if="canTrialMcp" class="btn btn-primary" type="button" @click="debugCap = stubFromCap(cap)">{{ trialLabel('mcp') }}</button>
                <router-link
                  v-else-if="!authState.token"
                  :to="{ path: '/login', query: { redirect: route.fullPath } }"
                  class="btn btn-primary"
                >登录后试用</router-link>
                <span v-else-if="!joined" class="muted" style="font-size: 13px">先在右侧加入，再试用。</span>
              </div>
            </div>
            <p v-if="parentPluginId" class="overview-note">
              来自能力包
              <router-link :to="`/capabilities/${parentPluginId}`">打开能力包</router-link>
            </p>
            <div v-if="readmeHtml" class="readme-body" v-html="readmeHtml"></div>
            <div v-else class="readme-fallback">
              <h3 class="guide-title">介绍</h3>
              <p class="guide-lead muted">作者还没写 README，下面按类型整理了用法。</p>
              <dl class="fallback-facts">
                <div>
                  <dt>是什么</dt>
                  <dd>{{ kindHint?.what || cap.description || '—' }}</dd>
                </div>
                <div>
                  <dt>怎么用</dt>
                  <dd>{{ kindHint?.where || consumerHint }}</dd>
                </div>
                <div>
                  <dt>谁来跑</dt>
                  <dd>{{ kindHint?.whoRuns || '加入后按平台约定使用' }}</dd>
                </div>
                <div>
                  <dt>交付</dt>
                  <dd>{{ distributionPlain(cap.distribution) }}</dd>
                </div>
              </dl>
              <div v-if="introTags.length || cap.category" class="fallback-tags">
                <span v-if="cap.category" class="badge badge-primary">{{ cap.category }}</span>
                <span v-for="t in introTags" :key="t" class="badge">{{ t }}</span>
              </div>
              <div v-if="fallbackToolPreview.length" class="fallback-section">
                <div class="fallback-k">能调哪些工具</div>
                <ul class="guide-list">
                  <li v-for="t in fallbackToolPreview" :key="'fb-' + t.name">
                    <strong>{{ t.name }}</strong>
                    <template v-if="t.description"> · {{ t.description }}</template>
                  </li>
                </ul>
                <button
                  v-if="mcpToolRows.length > fallbackToolPreview.length"
                  class="btn btn-sm mt-8"
                  type="button"
                  @click="setContentTab('tools')"
                >查看全部 {{ mcpToolRows.length }} 个工具</button>
              </div>
              <div v-if="isAgent && (embeddedSkills.length || embeddedMcp.length)" class="fallback-section">
                <div class="fallback-k">内含能力</div>
                <p class="guide-lead">
                  <template v-if="embeddedSkills.length">{{ embeddedSkills.length }} 个技能</template>
                  <template v-if="embeddedSkills.length && embeddedMcp.length"> · </template>
                  <template v-if="embeddedMcp.length">{{ embeddedMcp.length }} 个连接器</template>
                  ，加入专家后会一并生效。
                </p>
                <button class="btn btn-sm" type="button" @click="setContentTab('bundle')">查看内含能力</button>
              </div>
              <div v-if="isPlugin && pluginComponents.length" class="fallback-section">
                <div class="fallback-k">包装组件</div>
                <p class="guide-lead">这个能力包装了 {{ pluginComponents.length }} 个组件。</p>
                <button class="btn btn-sm" type="button" @click="setContentTab('components')">查看组件</button>
              </div>
              <div v-if="isOwner || isAdmin" class="guide-note">
                补一份 README，别人会更容易理解这个能力。
                <div class="fallback-cta">
                  <router-link
                    v-if="preferOnlineEdit && onlineEditPath"
                    :to="onlineEditPath"
                    class="btn btn-sm btn-primary"
                  >去在线编辑</router-link>
                  <button
                    v-else-if="canEditPackage"
                    class="btn btn-sm btn-primary"
                    type="button"
                    @click="setContentTab('files')"
                  >去文件里补 README</button>
                  <button
                    v-else-if="canEdit"
                    class="btn btn-sm"
                    type="button"
                    @click="setContentTab('manage')"
                  >去管理</button>
                </div>
              </div>
            </div>
            <div v-if="exampleList.length && !showAskTrial" class="guide-block">
              <h3 class="guide-title">可以这样用</h3>
              <ul class="guide-list">
                <li v-for="(s, i) in exampleList" :key="'ex-'+i">
                  <span v-if="['skill', 'mcp', 'agent', 'plugin'].includes(cap.type)">{{ s }}</span>
                  <code v-else>{{ s }}</code>
                </li>
              </ul>
            </div>
            <div v-if="cap.changelog" class="guide-block">
              <h3 class="guide-title">本版更新</h3>
              <div class="guide-body prose">{{ cap.changelog }}</div>
            </div>
            <details v-if="showUsageMore" class="more-usage">
              <summary>更多用法</summary>
              <div v-if="isMcp && isPublished && usedByAgents.length" class="guide-block trial-block">
                <h3 class="guide-title">单独调这个连接器</h3>
                <p class="guide-lead">{{ mcpTrial.hint }}</p>
                <div class="trial-actions">
                  <button v-if="canTrialMcp" class="btn" type="button" @click="debugCap = stubFromCap(cap)">连接并调工具</button>
                  <span v-else-if="authState.token && !joined" class="muted" style="font-size: 13px">先加入，再试用。</span>
                </div>
              </div>
              <details v-if="isMcp && mcpClientConfigJson" class="guide-block mcp-advanced">
                <summary class="mcp-advanced-summary">给兼容客户端的配置</summary>
                <div class="flex" style="gap: 8px; margin: 8px 0">
                  <button class="btn btn-sm" type="button" @click="copyMcpClientConfig">复制配置</button>
                </div>
                <pre class="mcp-config-pre">{{ mcpClientConfigJson }}</pre>
                <p v-if="mcpConfigNotice" class="muted" style="font-size: 12px; margin: 8px 0 0">{{ mcpConfigNotice }}</p>
              </details>
              <div v-if="(cap.artifacts || []).length && canViewPackage" class="guide-block">
                <button class="btn btn-sm" type="button" @click="setContentTab('files')">预览包内文件</button>
              </div>
              <div v-if="(isOwner || isAdmin) && (PACKAGE_HINTS[cap.type] || isAgent || isWorkflow)" class="guide-block">
                <h3 class="guide-title">给作者</h3>
                <ul v-if="PACKAGE_HINTS[cap.type]" class="guide-list">
                  <li>{{ PACKAGE_HINTS[cap.type] }}</li>
                </ul>
              </div>
              <div v-if="isOwner || isAdmin" class="guide-block">
                <h3 class="guide-title">调用方式</h3>
                <table class="table">
                  <thead><tr><th>方式</th><th>接口</th><th>适用</th></tr></thead>
                  <tbody>
                    <tr v-for="w in consumeWays" :key="w.id">
                      <td>{{ w.label }}</td>
                      <td><code style="font-size: 11px">{{ w.api }}</code></td>
                      <td class="muted" style="font-size: 12px">{{ w.who }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </details>
          </section>
          <section v-show="contentTab === 'config'" class="panel">
            <h2 class="detail-section-title">配置</h2>

            <div v-if="isMcp" class="guide-block">
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
                  <template v-if="platformSecretsReady === true">云端已可用。下面只影响你自己的本地安装，不填也能试用。</template>
                  <template v-else-if="isOwner || isAdmin">只影响本机安装。试用走上方平台密钥，未填的可选项不影响云端。</template>
                  <template v-else>只影响你自己的本地安装。云端由作者配置的平台密钥提供，可选项未填不影响云端使用。</template>
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
                        <span v-if="row.capRow" class="badge badge-success">本机已填</span>
                        <span v-else-if="row.globalRow" class="badge badge-warning">全局值生效</span>
                        <span v-else-if="row.secret" class="badge">未填</span>
                        <span v-else class="muted" style="font-size: 12px">可选</span>
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
          </section>

          <div v-if="contentTab === 'intro' && usedBy.length" class="panel">
            <h3>{{ usedByAgents.length && usedByAgents.length === usedBy.length ? '被以下专家使用' : '被以下能力使用' }}</h3>
            <div class="muted" style="font-size: 13px">
              {{ isMcp ? '挂到这些专家后，对话里才会动手。' : '来自专家内嵌声明或能力包组件引用。' }}
              <template v-if="usedByRawCount > usedBy.length">同一专家只显示最新版本。</template>
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
                    >{{ trialLabel('agent') }}</button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <details v-if="contentTab === 'intro' && cap.type === 'tool' && Object.keys(cap.input_schema || {}).length && !(cap.input_schema || {}).kind" class="panel more-usage">
            <summary>参数定义（schema.json）</summary>
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
          </details>

          <details v-if="contentTab === 'intro' && isAgent && cap.status === 'published'" class="a2a-panel panel more-usage">
            <summary>A2A 互调信息</summary>
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
          </details>
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
            <h2 class="detail-section-title">文件</h2>
            <p class="muted" style="font-size: 13px; margin: 0 0 12px">
              <template v-if="canEditPackage">
                多文件/文件夹在线编辑：新增文件与文件夹、上传多个文件或整个文件夹、重命名/删除；技能可用「技能模板」一键生成 SKILL.md 与 references/、scripts/、assets/。保存后写入能力包。
              </template>
              <template v-else-if="cap.status === 'reviewing'">
                审核中不能修改这一包。{{ isOwner ? '要改请先撤回。' : '' }}
              </template>
              <template v-else>浏览能力包内文件；Markdown 渲染预览，其它文本以源码显示。</template>
            </p>
            <PackageEditor
              v-if="canEditPackage"
              :capability-id="cap.id"
              :can-edit="true"
              :type="cap.type"
              :name="cap.name"
              @saved="load"
            />
            <PackagePreview v-else-if="(cap.artifacts || []).length" :capability-id="cap.id" />
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
                    <button
                      v-if="(isOwner || isAdmin) && ['draft', 'returned', 'rejected', 'reviewing'].includes(v.status)"
                      class="btn btn-sm btn-danger"
                      type="button"
                      @click="askDeleteVersion(v)"
                    >删除</button>
                  </td>
                </tr>
              </tbody>
            </table>
            <div v-if="isOwner" class="mt-24">
              <h3>版本管理</h3>
              <div class="muted" style="font-size: 12px; margin-bottom: 8px">
                <template v-if="['published', 'deprecated'].includes(cap.status)">
                  已发布内容请先创建新版本草稿，在线编辑完善后再提交审核。
                </template>
                <template v-else-if="preferOnlineEdit">
                  草稿请在线编辑完善（保存会生成能力包），再提交审核。
                </template>
                <template v-else>草稿需在编辑页完善内容后再提交审核。</template>
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
          </section>
        </div>

        <div v-show="contentTab === 'manage'">
          <h3 class="manage-group">基本信息</h3>
          <div v-if="isOwner || isAdmin" class="panel">
            <h3>能力头像</h3>
            <p class="muted" style="font-size: 13px; margin: 6px 0 0">
              建议使用 1:1 图片；选择后自动居中裁剪为 512×512 上传（PNG/JPG/WebP，≤256KB）。没有头像时显示名称首字。
            </p>
            <div class="flex mt-16" style="align-items: center; gap: 16px; flex-wrap: wrap">
              <img
                v-if="cap.icon_url && !iconFailed"
                class="icon-preview icon-img"
                :src="assetUrl(cap.icon_url)"
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

          <h3 class="manage-group">发布操作</h3>
            <div v-if="canSubmit || canWithdraw || canDelete || preferOnlineEdit || isAdmin" class="panel">
            <div class="flex flex-wrap" style="gap: 8px">
              <button
                v-if="preferOnlineEdit && !canRevise"
                class="btn"
                :class="canEdit ? 'btn-primary' : ''"
                type="button"
                :disabled="revising"
                @click="goEdit"
              >{{ cap.type === 'workflow' ? '查看编排' : '在线编辑' }}</button>
              <button
                v-if="isWorkflow && ['published', 'deprecated'].includes(cap.status)"
                class="btn"
                type="button"
                @click="router.push(`/workflows/${props.id}/chat`)"
              >对话调试</button>
              <button
                v-if="canSubmit"
                class="btn btn-success"
                type="button"
                @click="doAction(`/publish/capabilities/${props.id}/submit`)"
              >提交审核</button>
              <button v-if="canWithdraw" class="btn" type="button" @click="withdrawReview">撤回审核</button>
              <button v-if="canDelete" class="btn btn-danger" type="button" @click="askRemoveCap">删除</button>
              <button v-if="isAdmin && cap.status === 'published'" class="btn btn-danger" type="button" @click="askStatus('deprecate')">下架</button>
              <button v-if="isAdmin && ['published', 'deprecated', 'rejected', 'returned'].includes(cap.status)" class="btn btn-danger" type="button" @click="askStatus('archive')">归档</button>
            </div>
          </div>

          <div v-if="canRevise" class="panel">
            <div class="flex-between flex-wrap">
              <h3>在线编辑</h3>
              <span class="muted" style="font-size: 12px">基于当前已发布版本开新版（自动继承能力包），编辑/替换后再提交审核</span>
            </div>
            <div class="flex mt-16" style="gap: 10px; flex-wrap: wrap">
              <button class="btn btn-primary" type="button" :disabled="revising" @click="openEditDraft">
                {{ revising ? '正在开新版…' : '在线编辑（开新版）' }}
              </button>
            </div>
            <div v-if="reviseError" class="error mt-12">{{ reviseError }}</div>
          </div>

          <h3 class="manage-group">治理</h3>
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
                <label>用户白名单（多选，留空不限）</label>
                <MultiSelect
                  v-model="allowedUsers"
                  :options="userOptions"
                  placeholder="搜索并选择用户（姓名/工号）"
                  allow-create
                />
              </div>
              <div class="field">
                <label>部门白名单（多选，留空不限）</label>
                <MultiSelect
                  v-model="allowedDepartments"
                  :options="accessDepartmentOptions"
                  placeholder="搜索并选择部门（可手输新增）"
                  allow-create
                />
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
        </div>
      </div>

      <aside class="detail-aside">
        <div v-if="isPublished" class="install-card sticky-card">
          <h2 class="install-title">{{ installTitle }}</h2>
          <p class="install-lead">{{ consumerHint }}</p>
          <div class="install-actions">
            <button
              v-if="nextStep?.kind === 'join'"
              class="btn btn-block btn-primary"
              type="button"
              :disabled="!canSubscribe || myBusy"
              :title="canSubscribe ? '' : subscribeBlockedReason"
              @click="toggleMy"
            >{{ myBusy ? '加入中…' : nextStep.label }}</button>
            <router-link
              v-else-if="nextStep?.kind === 'login'"
              :to="{ path: '/login', query: { redirect: route.fullPath } }"
              class="btn btn-block btn-primary"
            >{{ nextStep.label }}</router-link>
            <button
              v-else-if="nextStep?.kind === 'trial-agent'"
              class="btn btn-block btn-primary"
              type="button"
              @click="openTrialAgent(usedByAgents[0])"
            >{{ nextStep.label }}</button>
            <button
              v-else-if="nextStep?.kind === 'trial'"
              class="btn btn-block btn-primary"
              type="button"
              @click="openTrial"
            >{{ nextStep.label }}</button>
            <router-link
              v-else-if="nextStep?.kind === 'mine'"
              to="/my"
              class="btn btn-block btn-primary"
            >{{ nextStep.label }}</router-link>
          </div>
          <p v-if="nextStep?.kind === 'join' && !canSubscribe" class="install-lead aside-deny">{{ subscribeBlockedReason }}</p>
          <p v-if="copyNotice || myNotice" class="install-lead">{{ copyNotice || myNotice }}</p>
          <div v-if="hasExtraWays" class="install-extra">
            <button
              v-if="joined && (cap.install_policy || 'optional') !== 'required'"
              class="btn btn-block install-ghost"
              type="button"
              :disabled="myBusy"
              @click="toggleMy"
            >{{ myBusy ? '处理中…' : '移出' }}</button>
            <button
              v-if="canLocalInstall && installCommand"
              class="btn btn-block install-ghost"
              type="button"
              @click="copyInstallCommand"
            >复制安装命令</button>
            <button v-if="!isRemoteOnly" class="btn btn-block install-ghost" type="button" @click="downloadArtifact">
              下载压缩包{{ packageSizeLabel ? ` · ${packageSizeLabel}` : '' }}
            </button>
            <button
              v-if="authState.token"
              class="btn btn-block install-ghost"
              type="button"
              :disabled="subBusy"
              @click="toggleSubscribe"
            >{{ subscribed ? '取消订阅更新' : '有更新时通知我' }}</button>
          </div>
        </div>
        <div v-else class="install-card action-card sticky-card">
          <h2 class="install-title">{{ canReview ? '审核' : '下一步' }}</h2>
          <template v-if="canReview">
            <div class="check-list">
              <label v-for="(item, i) in activeChecks" :key="item.index" class="check-item">
                <input v-model="reviewChecks[i]" type="checkbox" />
                <span class="check-label">{{ item.text }}</span>
                <em v-if="reviewAuto[i]" class="auto-tag">{{ item.index === 1 ? '未见硬编码' : '结构通过' }}</em>
              </label>
            </div>
            <textarea v-model="reviewComment" class="textarea" rows="2" placeholder="审核意见（拒绝 / 打回必填，作者会在详情页看到）"></textarea>
            <p class="aside-hint muted">
              <template v-if="approveBlock">{{ approveBlock }}</template>
              <template v-else-if="allReviewChecked">清单已齐，可以通过。{{ replacesVersion ? `已上架的 v${replacesVersion} 会变为已弃用。` : '' }}</template>
              <template v-else>还差 {{ pendingCheckLabels.length }} 项：{{ pendingCheckLabels.join('、') }}</template>
            </p>
            <div class="aside-cta">
              <button
                class="btn btn-block btn-lg btn-primary"
                type="button"
                :disabled="!allReviewChecked || !!approveBlock"
                @click="askApprove"
              >通过并上架</button>
            </div>
            <div class="aside-more">
              <button class="aside-link" type="button" @click="askReview('return')">打回</button>
              <button class="aside-link danger" type="button" @click="askReview('reject')">拒绝</button>
            </div>
          </template>
          <template v-else>
            <p class="aside-hint muted" style="margin-top: 0">{{ ownerNextHint }}</p>
            <div class="aside-cta">
              <button
                v-if="canSubmit"
                class="btn btn-block btn-lg btn-success"
                type="button"
                @click="doAction(`/publish/capabilities/${props.id}/submit`)"
              >提交审核</button>
              <router-link
                v-if="preferOnlineEdit && (canEdit || isAdmin)"
                :to="onlineEditPath"
                class="btn btn-block"
                :class="canSubmit ? '' : 'btn-primary btn-lg'"
              >在线编辑</router-link>
              <button v-if="canWithdraw" class="btn btn-block" type="button" @click="withdrawReview">撤回审核</button>
            </div>
          </template>
        </div>
        <div class="rating-card">
          <h2 class="install-title">评分与评论</h2>
          <div v-if="!authState.token" class="install-lead">
            <router-link :to="{ path: '/login', query: { redirect: route.fullPath } }">登录</router-link>
            后可以评分
          </div>
          <div v-else class="rating-form">
            <div class="rating-stars" role="radiogroup" aria-label="评分" @mouseleave="ratingHover = 0">
              <button
                v-for="s in 5"
                :key="s"
                type="button"
                class="rating-star"
                :class="{ on: s <= (ratingHover || rating.score), pop: ratingPop === s }"
                role="radio"
                :aria-checked="rating.score === s"
                :aria-label="`${s} 星`"
                @mouseenter="ratingHover = s"
                @focus="ratingHover = s"
                @blur="ratingHover = 0"
                @click="setRating(s)"
              >★</button>
            </div>
            <input v-model="rating.comment" class="input" placeholder="写下你的评价" @keyup.enter="submitRating" />
            <button class="btn btn-primary btn-sm" type="button" :disabled="ratingBusy" @click="submitRating">{{ ratingBusy ? '提交中…' : '提交' }}</button>
          </div>
          <div class="rating-list">
            <div v-if="ratings.length === 0" class="muted rating-empty">暂无评价</div>
            <div v-for="r in visibleRatings" :key="r.id" class="rating-item">
              <div class="flex-between">
                <strong>{{ r.username }}</strong>
                <span class="stat-stars">{{ stars(r.score) }}</span>
              </div>
              <div class="muted">{{ r.comment || '（无评论）' }}</div>
            </div>
            <button
              v-if="ratings.length > 3"
              class="aside-link"
              type="button"
              @click="ratingsExpanded = !ratingsExpanded"
            >{{ ratingsExpanded ? '收起评价' : `查看全部 ${ratings.length} 条评价` }}</button>
          </div>
        </div>
        <div class="panel resource-card">
          <p class="action-kicker">信息</p>
          <dl class="resource-list">
            <div v-if="cap.category"><dt>分类</dt><dd>{{ cap.category }}</dd></div>
            <div><dt>交付</dt><dd>{{ distributionPlain(cap.distribution) }}</dd></div>
          </dl>
          <p v-if="platformSecretsReady === false" class="aside-hint meta-warn">云端密钥还没配齐，配好后才能试用。</p>
          <details class="aside-fold">
            <summary>更多</summary>
            <dl class="aside-meta">
              <div><dt>谁能看</dt><dd>{{ VISIBILITY_LABELS[cap.visibility] || cap.visibility }}</dd></div>
              <div v-if="accessRestrictions.length"><dt>访问</dt><dd>{{ accessRestrictions.join('；') }}</dd></div>
              <div v-if="isMcp"><dt>连接方式</dt><dd>{{ mcpTransport }}</dd></div>
              <div><dt>货架</dt><dd>{{ shelfName || '—' }}</dd></div>
              <div><dt>风险</dt><dd>{{ RISK_DEFAULT_LABELS[cap.risk_default] || cap.risk_default || '—' }}</dd></div>
              <div><dt>数据域</dt><dd>{{ cap.data_domain || '—' }}</dd></div>
              <div v-if="cap.slug"><dt>标准名</dt><dd class="mono">{{ cap.slug }}</dd></div>
              <div v-if="provenance.origin"><dt>来源</dt><dd>{{ provenance.registry_name || provenance.origin }}</dd></div>
              <div v-if="requiresBinary"><dt>依赖 CLI</dt><dd class="mono">{{ requiresBinary }}</dd></div>
              <div v-if="isMcp && mcpToolRows.length"><dt>工具</dt><dd>{{ mcpToolRows.length }} 个</dd></div>
            </dl>
          </details>
        </div>
      </aside>
    </div>
    <ConfirmActionModal
      :show="!!confirmState"
      :title="confirmState?.title || ''"
      :body="confirmState?.body || ''"
      :ok-text="confirmState?.okText || '确定'"
      :danger="!!confirmState?.danger"
      @ok="onConfirmOk"
      @cancel="confirmState = null"
    />
    <DebugCapabilityModal
      :show="!!debugCap"
      :cap="debugCap"
      :title="trialLabel(debugCap?.type)"
      @close="debugCap = null"
    />
  </div>
</template>

<style scoped>
.detail {
  padding: 0 0 48px;
}
.detail-crumb {
  display: flex; flex-wrap: wrap; gap: 6px; align-items: center;
  font-size: 13px; margin-bottom: 14px;
}
.detail-crumb a { color: var(--muted); }
.detail-crumb a:hover { color: var(--primary); }
.detail-back {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  margin-right: 6px;
  padding: 0;
  border: none;
  border-radius: 6px;
  background: none;
  color: var(--text);
  cursor: pointer;
}
.detail-back:hover { background: var(--panel-2); color: var(--primary); }
.detail-hero {
  margin: 0 0 18px;
  padding: 20px 24px;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
}
.hero-top {
  display: grid;
  grid-template-columns: 56px minmax(0, 1fr);
  gap: 18px;
  align-items: start;
}
.hero-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.hero-cta { flex: none; width: 148px; }
.hero-hint {
  margin: 10px 0 0;
  max-width: 40rem;
  font-size: 13px;
  line-height: 1.55;
  color: var(--muted);
}
.detail-hero-icon {
  width: 56px; height: 56px; border-radius: 12px;
  background: var(--primary);
  color: #fff; font-size: 22px; font-weight: 650;
  display: flex; align-items: center; justify-content: center;
}
.icon-img { object-fit: cover; }
.detail-hero-icon.icon-img { background: var(--panel-2); }
.icon-preview {
  width: 72px; height: 72px; border-radius: 14px;
  display: flex; align-items: center; justify-content: center;
  object-fit: cover; background: var(--panel-2);
}
.icon-preview-fallback {
  background: linear-gradient(145deg, var(--primary), var(--primary-2));
  color: #fff; font-size: 26px; font-weight: 700;
}
.detail-hero-main { min-width: 0; }
.detail-hero-badges { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
.detail-title {
  margin: 0;
  font-size: clamp(23px, 2.6vw, 28px); line-height: 1.25;
  letter-spacing: -0.02em; font-weight: 700;
}
.hero-slug {
  margin: 4px 0 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px; line-height: 20px; color: var(--muted);
}
.hero-source {
  margin: 8px 0 0;
  display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
  font-size: 13px; color: var(--text);
}
.hero-facts {
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
  margin-top: 20px; font-size: 13px; color: var(--muted);
}
.fact-pill {
  border: 1px solid var(--border);
  background: var(--panel-2);
  border-radius: 999px;
  padding: 2px 8px;
  font-size: 12px;
  color: var(--muted);
}
.detail-ver {
  font-size: 16px; font-weight: 600; color: var(--muted);
  background: var(--panel-2); border: 1px solid var(--border);
  border-radius: 999px; padding: 2px 10px;
}
.detail-tagline {
  margin: 16px 0 0; color: var(--text); font-size: 15px;
  line-height: 26px; max-width: 920px;
}
.detail-byline { margin-top: 6px; font-size: 13px; display: flex; flex-wrap: wrap; gap: 6px; }
.stat-line {
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
  margin-top: 12px; font-size: 13px; color: var(--muted);
}
.stat-stars { color: #e0a106; letter-spacing: 1px; }
.stat-dot { color: var(--border-strong); }
.detail-tags { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.detail-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 40px;
  align-items: start;
}
.detail-main {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
  background: transparent;
  border: none;
  box-shadow: none;
}
.detail-aside { display: flex; flex-direction: column; gap: 12px; }
.detail-tabs {
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
  margin-bottom: 4px; padding: 0;
  background: transparent; border: none;
  border-radius: 0; box-shadow: none;
}
.detail-tab {
  border: 1px solid var(--border); background: var(--panel); color: var(--muted);
  padding: 8px 14px; border-radius: 999px; cursor: pointer; font-size: 13px;
}
.detail-tab:hover { color: var(--text); background: var(--panel); border-color: var(--primary); }
.detail-tab.active {
  color: #fff; background: var(--primary); border-color: var(--primary); font-weight: 500;
}
.detail-main .intro-stack { gap: 16px; }
.install-card {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 24px;
  box-shadow: var(--shadow);
}
.install-title {
  margin: 0;
  text-align: center;
  font-size: 16px;
  font-weight: 650;
  line-height: 24px;
}
.install-lead {
  margin: 16px 0 0;
  text-align: center;
  font-size: 12px;
  line-height: 22px;
  color: var(--muted);
}
.install-actions { margin-top: 16px; }
.install-actions .btn { width: 100%; height: 40px; }
.install-extra { display: flex; flex-direction: column; gap: 8px; margin-top: 12px; }
.install-ghost {
  width: 100%;
  height: 40px;
  background: var(--panel);
  border-color: var(--border-strong);
  box-shadow: none;
}
.install-ghost:hover { background: var(--panel-2); color: var(--text); border-color: var(--border-strong); }
.sticky-card { position: sticky; top: 16px; z-index: 1; }
.owner-progress.panel {
  display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
  margin-bottom: 16px; padding: 12px 16px;
}
.owner-progress-step {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 3px 10px 3px 3px; border-radius: 999px;
  border: 1px solid var(--border); background: var(--panel-2);
  font-size: 12px; color: var(--muted);
}
.owner-progress-step .n {
  width: 20px; height: 20px; border-radius: 50%;
  display: inline-flex; align-items: center; justify-content: center;
  background: var(--panel); border: 1px solid var(--border);
  font-weight: 650; font-size: 11px;
}
.owner-progress-step.done { color: var(--text); background: var(--primary-soft); border-color: transparent; }
.owner-progress-step.done .n { background: var(--panel); border-color: transparent; color: var(--primary); }
.owner-progress-step.active { color: #fff; font-weight: 600; background: var(--primary); border-color: var(--primary); }
.owner-progress-step.active .n { background: var(--panel); border-color: transparent; color: var(--primary); }
.action-kicker {
  margin: 0 0 10px; font-size: 12px; font-weight: 650;
  color: var(--muted); letter-spacing: 0.04em;
}
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
.aside-hint { margin: 10px 0 0; font-size: 12px; line-height: 1.6; }
.meta-warn { margin: 0 0 8px; color: #b7791f; }
.aside-deny { color: var(--danger); }
.access-lists { display: grid; gap: 12px; max-width: 640px; }
.role-options { display: flex; flex-wrap: wrap; gap: 14px; }
.role-option {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 13px; cursor: pointer;
}
.aside-more {
  display: flex; flex-direction: column; align-items: flex-start; gap: 8px;
  margin: 2px 0 6px;
}
.action-card .aside-link { text-decoration: none; }
.action-card .aside-link:hover { text-decoration: underline; }
.aside-link {
  border: none; background: transparent; padding: 0;
  color: var(--muted); font-size: 12px; cursor: pointer; text-decoration: underline;
}
.aside-link:hover { color: var(--primary); }
.aside-link.danger,
.aside-link.danger:hover { color: var(--danger); }
.intro-stack { display: flex; flex-direction: column; gap: 16px; }
.overview {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.overview .guide-block + .guide-block {
  margin-top: 0;
  padding-top: 0;
  border-top: none;
}
.overview-note { margin: 0; font-size: 13px; color: var(--muted); }
.readme-fallback { min-width: 0; }
.fallback-facts { margin: 12px 0 0; display: grid; gap: 0; }
.fallback-facts > div {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
  line-height: 1.55;
}
.fallback-facts > div:last-child { border-bottom: none; }
.fallback-facts dt { margin: 0; color: var(--muted); font-weight: 500; }
.fallback-facts dd { margin: 0; color: var(--text); }
.fallback-tags { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.fallback-section { margin-top: 14px; }
.fallback-k {
  font-size: 12px;
  font-weight: 650;
  color: var(--muted);
  margin-bottom: 6px;
}
.fallback-cta { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.resource-card {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 8px 16px 12px;
}
.resource-card .action-kicker { letter-spacing: 0; color: var(--text); font-size: 13px; margin: 10px 0 4px; }
.resource-list { margin: 0; }
.resource-list > div {
  display: flex; justify-content: space-between; gap: 16px;
  padding: 9px 0; border-bottom: 1px solid var(--border); font-size: 13px;
}
.resource-list > div:last-child { border-bottom: none; }
.resource-list dt { margin: 0; color: var(--muted); }
.resource-list dd { margin: 0; text-align: right; }
.tab-split {
  margin-left: auto; align-self: center;
  padding: 0 8px; border: none;
  font-size: 11px; color: var(--muted);
}
.more-usage { margin-top: 4px; }
.more-usage > summary {
  cursor: pointer; font-size: 13px; font-weight: 600; color: var(--muted);
  list-style: none;
}
.more-usage > summary::-webkit-details-marker { display: none; }
.more-usage > summary:hover { color: var(--text); }
.more-usage .guide-block:first-of-type { margin-top: 14px; }
.check-list { display: flex; flex-direction: column; gap: 4px; margin-bottom: 10px; }
.check-item {
  display: flex; align-items: flex-start; gap: 8px;
  min-height: 28px; font-size: 13px; cursor: pointer;
}
.check-label { flex: 1; min-width: 0; }
.auto-tag {
  font-style: normal; font-size: 11px; font-weight: 500;
  color: #0e9f5c; background: rgba(18, 183, 106, 0.1);
  border-radius: 999px; padding: 1px 8px; white-space: nowrap;
}
.action-card textarea { width: 100%; min-height: 0; margin: 0 0 8px; }
.action-card {
  max-height: calc(100vh - 32px);
  overflow: auto;
  padding: 16px;
}
.meta-card { padding: 6px 16px 10px; box-shadow: none; }
.aside-fold { margin-top: 8px; }
.aside-fold > summary {
  cursor: pointer; list-style: none;
  display: flex; align-items: center; justify-content: space-between;
  min-height: 32px; font-size: 12px; color: var(--muted);
}
.aside-fold > summary::-webkit-details-marker { display: none; }
.aside-fold > summary::after {
  content: '';
  width: 6px; height: 6px; flex: none;
  border-right: 1.5px solid var(--muted);
  border-bottom: 1.5px solid var(--muted);
  transform: rotate(45deg) translateY(-2px);
}
.aside-fold[open] > summary::after { transform: rotate(-135deg) translateY(-1px); }
.aside-fold > summary:hover { color: var(--text); }
.aside-fold .aside-meta { margin-top: 4px; padding-top: 4px; border-top: none; }
.aside-meta {
  margin: 0; padding-top: 0; border-top: none;
  display: grid; gap: 8px;
}
.aside-meta > div { display: grid; grid-template-columns: 72px 1fr; gap: 8px; font-size: 13px; }
.aside-meta dt { margin: 0; color: var(--muted); }
.aside-meta dd { margin: 0; color: var(--text); }
.detail-section-title { margin: 0 0 14px; font-size: 16px; font-weight: 650; }
.detail-main .table { margin: 0; }
.detail-main .table th { font-size: 12px; background: transparent; }
/* 管理 tab 分组标题：把「基本信息 / 发布操作 / 治理」分开，避免堆叠 */
.manage-group {
  margin: 22px 0 8px;
  font-size: 13px;
  font-weight: 650;
  color: var(--muted);
  letter-spacing: 0.02em;
}
.manage-group:first-child { margin-top: 0; }
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
.rating-card {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 20px 16px 8px;
}
.rating-form {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 8px;
  margin-top: 14px;
}
.rating-form .input { width: 100%; min-width: 0; }
.rating-list { margin-top: 12px; }
.rating-empty { padding: 8px 0 12px; font-size: 13px; }
.rating-stars { display: flex; justify-content: center; gap: 2px; }
.rating-form .btn { width: 100%; }
.rating-star {
  border: none; background: transparent; padding: 0 1px;
  font-size: 26px; line-height: 1; color: var(--border-strong); cursor: pointer;
  transition: color .15s ease, transform .18s cubic-bezier(.2, 1.4, .4, 1);
}
.rating-star.on { color: #f5b400; }
.rating-star.pop { animation: star-pop .32s ease; }
@keyframes star-pop {
  0% { transform: scale(1); }
  40% { transform: scale(1.32); }
  100% { transform: scale(1); }
}
.rating-item { padding: 10px 0; border-bottom: 1px solid var(--border); }
.rating-item:last-child { border-bottom: none; }
.a2a-panel { border: none; }
.url-code {
  background: var(--panel-2); padding: 4px 10px; border-radius: 6px;
  font-size: 12px; color: #2451c7; word-break: break-all;
}
.row-current td { background: rgba(79, 140, 255, 0.06); }
.run-output {
  margin: 8px 0 0;
  padding: 10px 12px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 320px;
  overflow: auto;
}
.run-exec { display: flex; align-items: center; gap: 8px; padding: 6px 0; border-bottom: 1px solid var(--border); }
.run-exec:last-child { border-bottom: none; }
.mt-8 { margin-top: 8px; } .mt-12 { margin-top: 12px; } .mt-16 { margin-top: 16px; } .mt-24 { margin-top: 24px; }
h3 { margin: 0 0 12px; }
@media (max-width: 960px) {
  .detail-layout { grid-template-columns: 1fr; gap: 20px; }
  .detail-aside { order: -1; }
  .hero-top { grid-template-columns: 48px minmax(0, 1fr); gap: 14px; }
  .detail-title { font-size: 22px; line-height: 30px; }
  .detail-hero-icon { width: 48px; height: 48px; font-size: 18px; border-radius: 10px; }
  .sticky-card { position: static; }
}
</style>
