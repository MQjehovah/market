<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
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
  KIND_HINTS,
  ORCH_LABELS,
  CONSUME_WAYS,
  REVIEW_CHECKLIST,
  OWNER_PROGRESS_STEPS,
  JOIN_VS_INSTALL_HINT,
  INSTALL_POLICY_LABELS,
  shelfLabel,
  formatDate,
  stars,
  formatSize,
  needsZipUpload,
  SCENARIO_HINTS,
  EXAMPLE_PROMPTS,
  installCommandFor,
  isLocalInstallKind,
  ownerProgressIndex,
  editRouteFor,
  canOnlineEdit,
  mcpTrialMode
} from '../utils/format'
import StatusBadge from '../components/StatusBadge.vue'
import PackagePreview from '../components/PackagePreview.vue'
import DebugCapabilityModal from '../components/DebugCapabilityModal.vue'

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
const copyNotice = ref('')
const accessPolicy = ref('open')
const installPolicy = ref('optional')
const allowedUsers = ref('')
const accessSaved = ref('')

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
  visibility: 'internal'
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
const kindHint = computed(() => (cap.value ? KIND_HINTS[cap.value.type] : null))
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
const canLocalInstall = computed(() => (cap.value ? isLocalInstallKind(cap.value.type) : false))
const scenarioList = computed(() => {
  if (!cap.value) return []
  const schema = cap.value.input_schema || {}
  const custom = schema.scenarios || schema.use_cases
  if (Array.isArray(custom) && custom.length) return custom.map(String)
  return SCENARIO_HINTS[cap.value.type] || []
})
const exampleList = computed(() => {
  if (!cap.value) return []
  const schema = cap.value.input_schema || {}
  const custom = schema.examples || schema.example_prompts
  if (Array.isArray(custom) && custom.length) return custom.map(String)
  const raw = EXAMPLE_PROMPTS[cap.value.type] || []
  return raw.map((s) => s.replaceAll('<name>', cap.value.name).replaceAll('<plugin>', cap.value.name))
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

const mcpTrial = computed(() =>
  mcpTrialMode({
    transport: mcpTransport.value,
    envKeys: mcpEnvRows.value.map((r) => r.key)
  })
)
const joined = computed(() => Boolean(cap.value && myIds.value.has(cap.value.id)))
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
const consumeActionLabel = computed(() => {
  if (isMcp.value) return '使用'
  return canLocalInstall.value ? '安装' : '消费'
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
    const server = conn.server || schema.server || name
    entry.type = 'sse'
    entry.baseUrl = `${window.location.origin}${__API_BASE__}/mcp-gateway/${server}/sse`
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
  const keys = contentTabs.value.map((t) => t.key)
  if (!keys.includes(contentTab.value)) contentTab.value = 'intro'
})

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
  debugCap.value = stubFromCap(cap.value)
}

function openTrialAgent(u) {
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
    versions.value = await api.get(`/capabilities/${props.id}/versions`)
    ratings.value = await api.get(`/capabilities/${props.id}/ratings`)
    await loadMcpPackageMeta()
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
}

async function toggleMy() {
  myNotice.value = ''
  try {
    if (myIds.value.has(props.id)) {
      if ((cap.value?.install_policy || 'optional') === 'required') {
        myNotice.value = '必装能力不可移除'
        return
      }
      const r = await api.delete(`/my/capabilities/${props.id}`)
      const next = new Set(myIds.value)
      next.delete(props.id)
      myIds.value = next
      myNotice.value = r.message
    } else {
      const r = await api.post('/my/capabilities', { capability_id: props.id })
      myIds.value = new Set([...myIds.value, props.id])
      const cmd = installCommand.value
      myNotice.value = canLocalInstall.value && cmd
        ? `${r.message} 本地安装：${cmd}`
        : r.message
    }
  } catch (e) {
    myNotice.value = e.message
  }
}

function downloadTemplate() {
  if (!cap.value) return
  const name = encodeURIComponent(cap.value.name || 'example')
  window.open(`${__API_BASE__}/meta/package-templates/${cap.value.type}?name=${name}`, '_blank')
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
      allowed_users: allowedUsers.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
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

async function subscribe() {
  error.value = ''
  try {
    await api.post('/subscriptions', { capability_name: cap.value.name })
    notice.value = '订阅成功，新版本发布时将收到通知'
  } catch (e) {
    error.value = e.message
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
      visibility: editForm.visibility
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
      <router-link v-else-if="cap.type === 'agent'" :to="{ path: '/', query: { type: 'agent' } }">助手</router-link>
      <router-link v-else-if="cap.type === 'plugin'" :to="{ path: '/', query: { shelf: 'install' } }">安装包</router-link>
      <router-link v-else-if="shelfName" :to="{ path: '/', query: { type: cap.type } }">{{ TYPE_LABELS[cap.type] || shelfName }}</router-link>
      <span v-if="cap.type || shelfName">/</span>
      <span>{{ cap.name }}</span>
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
      <div class="detail-hero-icon" aria-hidden="true">{{ typeInitial }}</div>
      <div class="detail-hero-main">
        <div class="detail-hero-badges">
          <StatusBadge :status="cap.status" />
          <span v-if="cap.latest" class="badge badge-primary">最新版</span>
          <span class="badge">{{ TYPE_LABELS[cap.type] }}</span>
          <span v-if="shelfName" class="badge badge-primary">{{ shelfName }}</span>
          <span v-if="isMcp" class="badge">{{ mcpTransport }}</span>
          <span
            v-if="isMcp && isPublished"
            class="badge"
            :class="mcpTrial.canOneClick ? 'badge-success' : 'badge-warning'"
          >{{ mcpTrial.label }}</span>
          <span v-if="isMcp && mcpToolRows.length" class="badge badge-primary">{{ mcpToolRows.length }} 工具</span>
        </div>
        <h1 class="detail-title">
          {{ cap.name }}
          <span class="detail-ver">v{{ cap.version }}</span>
        </h1>
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
          <span v-for="t in (cap.tags || [])" :key="t" class="badge">{{ t }}</span>
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
            <div v-if="scenarioList.length" class="guide-block">
              <h3 class="guide-title">适用场景</h3>
              <ul class="guide-list">
                <li v-for="(s, i) in scenarioList" :key="'sc-'+i">{{ s }}</li>
              </ul>
            </div>

            <div v-if="isMcp" class="guide-block">
              <h3 class="guide-title">核心能力 · 工具</h3>
              <div v-if="mcpMetaLoading" class="muted" style="font-size: 13px">加载 tools.json…</div>
              <div v-else-if="!mcpToolRows.length" class="muted" style="font-size: 13px">
                暂无工具清单。可在能力包 <code>tools.json</code> 声明，或连接后由服务端发现。
              </div>
              <table v-else class="table">
                <thead><tr><th>工具名称</th><th>描述</th></tr></thead>
                <tbody>
                  <tr v-for="t in mcpToolRows" :key="t.name">
                    <td><code>{{ t.name }}</code></td>
                    <td class="muted">{{ t.description || '—' }}</td>
                  </tr>
                </tbody>
              </table>
              <button
                v-if="mcpToolRows.length"
                class="btn btn-sm mt-12"
                type="button"
                @click="contentTab = 'tools'"
              >查看全部工具</button>
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
                  <tr v-for="row in mcpEnvRows" :key="row.key">
                    <td><code>{{ row.key }}</code></td>
                    <td>
                      <span v-if="row.required" class="badge badge-danger">必填</span>
                      <span v-else class="muted">可选</span>
                    </td>
                    <td class="muted">
                      自行配置，占位 <code>{{ row.hint }}</code>（详情不展示明文密钥）
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div v-if="isMcp && isPublished" class="guide-block trial-block">
              <h3 class="guide-title">试用连接器</h3>
              <p class="guide-lead">{{ mcpTrial.hint }}</p>
              <p v-if="usedByAgents.length" class="muted" style="font-size: 13px; margin: 0 0 10px">
                单独调用工具只验证「手」能抓。要看到问答效果，请试用依赖它的助手。
              </p>
              <div class="trial-actions">
                <button
                  v-if="usedByAgents.length && authState.token"
                  class="btn btn-primary"
                  type="button"
                  @click="openTrialAgent(usedByAgents[0])"
                >试用助手 · {{ usedByAgents[0].name }}</button>
                <button
                  v-if="canTrialMcp"
                  class="btn"
                  :class="usedByAgents.length ? '' : 'btn-primary'"
                  type="button"
                  @click="openTrial"
                >试用连接器</button>
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
                给调试或兼容客户端粘贴 <code>mcpServers</code>。员工日常请加入后随助手安装，不要把这段当主路径。
              </p>
              <div class="flex" style="gap: 8px; margin-bottom: 8px">
                <button class="btn btn-sm" type="button" @click="copyMcpClientConfig">复制 JSON</button>
              </div>
              <pre class="mcp-config-pre">{{ mcpClientConfigJson }}</pre>
              <p v-if="mcpConfigNotice" class="muted" style="font-size: 12px; margin: 8px 0 0">{{ mcpConfigNotice }}</p>
            </details>

            <div v-if="exampleList.length" class="guide-block">
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
                  <li v-if="PACKAGE_HINTS[cap.type]"><strong>包规范</strong>：{{ PACKAGE_HINTS[cap.type] }}</li>
                </ul>
                <div v-if="isAgent" class="guide-note">
                  <strong>{{ ORCH_LABELS.team.name }}</strong>（TEAM.md）：节点是角色，在零号员工 TeamOrchestrator 执行。与「能力编排」平行，禁止互转。
                </div>
                <div v-if="isWorkflow" class="guide-note">
                  <strong>{{ ORCH_LABELS.capability.name }}</strong>：节点是已上架能力，只在云端执行；可将带 TEAM.md 的 Agent 作为 agent 节点调用。
                </div>
              </div>
            </div>
            <div class="guide-block">
              <h3 class="guide-title">怎么用 · 消费矩阵</h3>
              <div class="guide-body">
                <p class="guide-lead muted">市场是控制面目录。日常在零号员工问答里用；试用只验证连接，不是生产主路径。</p>
                <table class="table">
                  <thead><tr><th>方式</th><th>接口</th><th>适用</th></tr></thead>
                  <tbody>
                    <tr v-for="w in CONSUME_WAYS" :key="w.id">
                      <td>{{ w.label }}</td>
                      <td><code style="font-size: 11px">{{ w.api }}</code></td>
                      <td class="muted" style="font-size: 12px">{{ w.who }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </section>

          <div v-if="parentPluginId" class="panel">
            <h3>来自插件</h3>
            <div class="muted" style="font-size: 13px">本能力由插件拆包生成。</div>
            <div class="mt-16"><router-link :to="`/capabilities/${parentPluginId}`">查看父插件</router-link></div>
          </div>

          <div v-if="usedBy.length" class="panel">
            <h3>{{ usedByAgents.length && usedByAgents.length === usedBy.length ? '被以下助手使用' : '被以下能力使用' }}</h3>
            <div class="muted" style="font-size: 13px">
              {{ isMcp ? '挂到这些助手后，对话里才会动手。' : '来自助手内嵌声明或安装包组件引用。' }}
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
                    >试用助手</button>
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
            <h3>插件组件</h3>
            <div class="muted" style="font-size: 13px">上传 plugin zip 后自动拆出；一键加入会同时加入下列组件。</div>
            <div v-if="pluginComponents.length === 0" class="muted mt-12">尚未上传插件包，或包内无组件</div>
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
            <h3>包含的 Skills / MCP</h3>
            <div class="muted" style="font-size: 13px">从包内提取；若市场已收录可跳转。</div>
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
              <h4 style="margin: 0 0 8px">MCP（{{ embeddedMcp.length }}）</h4>
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
              <input
                v-if="accessPolicy === 'restricted'"
                v-model="allowedUsers"
                class="input"
                style="max-width: 260px"
                placeholder="白名单用户名（逗号分隔）"
              />
              <select v-model="installPolicy" class="select" style="max-width: 220px">
                <option value="optional">{{ INSTALL_POLICY_LABELS.optional }}</option>
                <option value="default_on">{{ INSTALL_POLICY_LABELS.default_on }}</option>
                <option value="required">{{ INSTALL_POLICY_LABELS.required }}</option>
              </select>
              <button class="btn btn-primary" type="button" @click="saveAccess">保存</button>
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
                市场 MCP 需 <code>mcp.json</code>、<code>connection.json</code>、<code>tools.json</code>、<code>security.json</code>。
                不能直接上传 agent 仓的 <code>mcp-server.json</code>。
              </div>
            </div>
          </div>
        </div>
      </div>

      <aside class="detail-aside">
        <div class="panel aside-card sticky-card">
          <h3>{{ isPublished ? consumeActionLabel : '下一步' }}</h3>
          <template v-if="isPublished">
            <div class="install-box" :class="{ muted: !canLocalInstall }">
              <code class="install-cmd">{{ installCommand }}</code>
              <button class="btn btn-sm" type="button" @click="copyInstallCommand">复制</button>
            </div>
            <p v-if="copyNotice" class="muted" style="font-size: 12px; margin: 8px 0 0">{{ copyNotice }}</p>
            <div class="aside-cta mt-16">
              <button
                v-if="authState.token"
                class="btn btn-block btn-lg"
                :class="myIds.has(cap.id) ? '' : 'btn-primary'"
                type="button"
                :disabled="myIds.has(cap.id) && (cap.install_policy || 'optional') === 'required'"
                @click="toggleMy"
              >
                {{
                  myIds.has(cap.id)
                    ? ((cap.install_policy || 'optional') === 'required'
                      ? '必装 · 已加入'
                      : (isPlugin ? '移出安装包' : '已加入'))
                    : (isPlugin ? '加入 · 安装包' : '加入')
                }}
              </button>
              <button
                v-if="usedByAgents.length && authState.token"
                class="btn btn-block"
                :class="myIds.has(cap.id) ? 'btn-primary' : ''"
                type="button"
                @click="openTrialAgent(usedByAgents[0])"
              >试用助手 · {{ usedByAgents[0].name }}</button>
              <button
                v-if="canTrialCurrent"
                class="btn btn-block"
                :class="usedByAgents.length || !myIds.has(cap.id) ? '' : 'btn-primary'"
                type="button"
                @click="openTrial"
              >{{ isAgent ? '试用助手' : '试用连接器' }}</button>
              <router-link
                v-else-if="(isMcp || isAgent) && !authState.token"
                :to="{ path: '/login', query: { redirect: route.fullPath } }"
                class="btn btn-block btn-primary"
              >登录后试用</router-link>
              <button
                class="btn btn-block"
                type="button"
                @click="downloadArtifact"
              >下载 zip{{ packageSizeLabel ? ` · ${packageSizeLabel}` : '' }}</button>
              <button v-if="authState.token && cap.status === 'published'" class="btn btn-block" type="button" @click="subscribe">订阅更新</button>
              <router-link v-if="authState.token" to="/my" class="btn btn-block">去我的能力</router-link>
              <span v-if="myNotice" class="muted" style="font-size: 12px; display: block; margin-top: 8px">{{ myNotice }}</span>
            </div>
            <p class="aside-hint muted">
              <template v-if="isMcp">
                {{ mcpTrial.hint }}
                {{ JOIN_VS_INSTALL_HINT }}
              </template>
              <template v-else-if="canLocalInstall">
                {{ JOIN_VS_INSTALL_HINT }}
              </template>
              <template v-else>
                本类型不支持本地 cap install。「加入」仅授权；请用上方云端接口或在能力编排中引用。
              </template>
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
            <div v-if="cap.category"><dt>分类</dt><dd>{{ cap.category }}</dd></div>
            <div><dt>作者</dt><dd>{{ cap.author_name || '-' }}</dd></div>
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
    <DebugCapabilityModal
      :show="!!debugCap"
      :cap="debugCap"
      :title="debugCap?.type === 'agent' ? '试用助手' : (debugCap?.type === 'mcp' ? '试用连接器' : '云端试用')"
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
