<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { marked } from 'marked'
import { api } from '../api'
import { authState } from '../stores/auth'
import { adminState } from '../stores/admin'
import {
  TYPE_LABELS,
  REVIEW_CHECKLIST,
  VISIBILITY_LABELS,
  shelfLabel,
  formatDate
} from '../utils/format'
import StatusBadge from '../components/StatusBadge.vue'
import DebugCapabilityModal from '../components/DebugCapabilityModal.vue'
import PackagePreview from '../components/PackagePreview.vue'
import AdminTrialPanel from '../components/AdminTrialPanel.vue'
import ConfirmActionModal from '../components/ConfirmActionModal.vue'

const route = useRoute()
const router = useRouter()

const __API_BASE__ = (import.meta.env.BASE_URL || '/').replace(/\/$/, '') + '/api'

const SECTIONS = ['review', 'listed', 'users', 'gateway', 'tokens']
const SECTION_META = {
  review: { label: '审核', hint: '处理待审、拒绝与打回；通过后到「上架治理」做下架归档' },
  listed: { label: '上架治理', hint: '已发布与已下架资产的下架、归档' },
  users: { label: '用户管理', hint: '账号、角色与启停；权限边界见下方角色说明' },
  gateway: { label: 'MCP 网关', hint: '把 stdio / HTTP / SSE 统一暴露为 HTTP 端点，供 Dify / Agent 接入' },
  tokens: {
    label: '服务令牌',
    hint: '给 Agent / CI / 网关消费方发 M2M Bearer（mkt_svc_…），免交互式登录'
  }
}

const section = computed(() => {
  const s = String(route.params.section || '')
  return SECTIONS.includes(s) ? s : 'review'
})
const sectionTitle = computed(() => SECTION_META[section.value].label)
const sectionHint = computed(() => SECTION_META[section.value].hint)

watch(
  () => route.params.section,
  (s) => {
    if (!SECTIONS.includes(String(s))) router.replace('/admin/review')
  },
  { immediate: true }
)

/** 审核队列：pending | rejected | returned */
const auditFilter = ref('pending')
const auditTypeFilter = ref('')
const listedFilter = ref('published')
const showTrial = ref(false)
const showStatsDetail = ref(false)
const selectedId = ref('')
const detailTab = ref('intro')
const reviewQueue = ref([])
const rejectedQueue = ref([])
const returnedQueue = ref([])
const allCaps = ref([])
const users = ref([])
const stats = ref(null)
const error = ref('')
const notice = ref('')
const reviewComment = ref('')
const reviewChecks = ref([])
const confirmReview = ref(null)
const allReviewChecked = computed(() => reviewChecks.value.length > 0 && reviewChecks.value.every(Boolean))
function resetReviewChecks() {
  reviewChecks.value = REVIEW_CHECKLIST.map(() => false)
}

const TYPE_COLORS = {
  plugin: '#2f6bff',
  agent: '#12b76a',
  workflow: '#7a5cff',
  skill: '#f5a524',
  mcp: '#0ea5e9',
  tool: '#64748b'
}

const userQuery = ref('')
const showCreateUser = ref(false)
const showEditUser = ref(false)
const editUser = ref(null)
const userNotice = ref('')
const userForm = ref({
  username: '',
  email: '',
  password: '',
  display_name: '',
  organization: '',
  role: 'user'
})
const editForm = ref({
  username: '',
  display_name: '',
  email: '',
  organization: '',
  password: ''
})
const debugType = ref('tool')
const debugName = ref('')
const debugCap = ref(null)
const gatewayServers = ref([])
const gatewayNotice = ref('')
const showGatewayModal = ref(false)
const gatewayForm = ref({
  id: '',
  name: '',
  description: '',
  transport: 'stdio',
  url: '',
  headers: '{}',
  command: '',
  args: '[]',
  env: '{}',
  cwd: '',
  api_token: '',
  capability_id: '',
  enabled: true
})
const gatewayTest = ref({})

const TOKEN_SCOPE_OPTS = [
  { key: 'runtime', label: 'runtime', desc: '云端 invoke / persona / mcp call' },
  { key: 'gateway', label: 'gateway', desc: '能力级 MCP 网关 /cap/…' },
  { key: 'sync', label: 'sync', desc: '目录同步 host-sync / capabilities/sync' },
  { key: 'admin', label: 'admin', desc: '管理接口（慎用）' }
]
const serviceTokens = ref([])
const tokenNotice = ref('')
const showTokenModal = ref(false)
const showCreatedToken = ref(false)
const createdTokenPlain = ref('')
const tokenForm = ref({
  name: '',
  scopes: ['runtime', 'gateway', 'sync'],
  expires_days: 365
})

const auditCounts = computed(() => ({
  pending: reviewQueue.value.length,
  rejected: rejectedQueue.value.length,
  returned: returnedQueue.value.length
}))

const listedCounts = computed(() => ({
  published: allCaps.value.filter((c) => c.status === 'published').length,
  deprecated: allCaps.value.filter((c) => c.status === 'deprecated').length,
  all: allCaps.value.filter((c) => ['published', 'deprecated'].includes(c.status)).length
}))

const auditList = computed(() => {
  let list =
    auditFilter.value === 'pending'
      ? reviewQueue.value
      : auditFilter.value === 'returned'
        ? returnedQueue.value
        : rejectedQueue.value
  if (auditTypeFilter.value) list = list.filter((c) => c.type === auditTypeFilter.value)
  return list
})

const selectedCap = computed(() => auditList.value.find((c) => c.id === selectedId.value) || null)

const selectedReadmeHtml = computed(() => {
  const md = (selectedCap.value?.readme_md || '').trim()
  if (!md) return ''
  try {
    return marked.parse(md, { gfm: true, breaks: true })
  } catch {
    return ''
  }
})

const selectedValidation = computed(() => {
  const cap = selectedCap.value
  if (!cap) return null
  return cap.validation_report || (cap.input_schema || {})._validation_report || null
})

const fileList = computed(() => {
  const files = selectedValidation.value?.files
  if (!files) return []
  if (Array.isArray(files)) return files.map(String)
  if (typeof files === 'object') return Object.keys(files)
  return []
})

const listedCaps = computed(() => {
  const all = allCaps.value.filter((c) => ['published', 'deprecated'].includes(c.status))
  if (listedFilter.value === 'all') return all
  return all.filter((c) => c.status === listedFilter.value)
})

function typeColor(type) {
  return TYPE_COLORS[type] || '#2f6bff'
}

function typeInitial(type) {
  return (TYPE_LABELS[type] || type || '?').slice(0, 1)
}

function riskOf(cap) {
  const vr = cap?.validation_report || (cap?.input_schema || {})._validation_report || {}
  const errors = vr.errors || []
  const warnings = vr.warnings || []
  if (errors.length) return { key: 'high', label: '高风险' }
  if (warnings.length) return { key: 'medium', label: '中风险' }
  return { key: 'none', label: '无风险' }
}

function selectCap(cap) {
  selectedId.value = cap.id
  detailTab.value = 'intro'
  resetReviewChecks()
  reviewComment.value = ''
}

function setAuditFilter(key) {
  auditFilter.value = key
  const list =
    key === 'pending' ? reviewQueue.value : key === 'returned' ? returnedQueue.value : rejectedQueue.value
  selectedId.value = list[0]?.id || ''
  resetReviewChecks()
  reviewComment.value = ''
}

watch(auditList, (list) => {
  if (!list.find((c) => c.id === selectedId.value)) {
    selectedId.value = list[0]?.id || ''
  }
})

const roleDefs = [
  { key: 'admin', label: '管理员', desc: '审核上架、下架归档、用户管理、试用全部资产、MCP 网关、服务令牌' },
  { key: 'publisher', label: '发布者', desc: '发布与在线编辑；提交审核；试用自己创建或已加入的资产' },
  { key: 'user', label: '普通用户', desc: '登录可发布草稿并在线编辑技能/助手等；可试用自己创建或已加入的资产；生产消费走 cap install / MCP' }
]

const filteredUsers = computed(() => {
  const q = userQuery.value.trim().toLowerCase()
  if (!q) return users.value
  return users.value.filter(
    (u) =>
      u.username.toLowerCase().includes(q) ||
      (u.display_name || '').toLowerCase().includes(q) ||
      (u.email || '').toLowerCase().includes(q)
  )
})

async function load() {
  error.value = ''
  try {
    const [queue, rejected, returned, caps, userList, stat, gatewayList, tokenList] = await Promise.all([
      api.get('/admin/capabilities?status_filter=reviewing'),
      api.get('/admin/capabilities?status_filter=rejected'),
      api.get('/admin/capabilities?status_filter=returned'),
      api.get('/admin/capabilities'),
      api.get('/admin/users'),
      api.get('/admin/stats'),
      api.get('/admin/mcp-gateway/servers'),
      api.get('/admin/service-tokens')
    ])
    reviewQueue.value = queue
    rejectedQueue.value = rejected
    returnedQueue.value = returned
    allCaps.value = caps
    users.value = userList
    stats.value = stat
    gatewayServers.value = gatewayList
    serviceTokens.value = tokenList
    adminState.reviewingCount = reviewQueue.value.length
    if (!selectedId.value && auditList.value.length) {
      selectedId.value = auditList.value[0].id
    }
  } catch (e) {
    error.value = e.message
  }
}

async function review(cap, action) {
  if (!cap) return
  try {
    await api.post(`/admin/capabilities/${cap.id}/review`, { action, comment: reviewComment.value })
    notice.value =
      action === 'approve'
        ? `「${cap.name}」已通过并上架`
        : action === 'reject'
          ? `「${cap.name}」已拒绝`
          : `「${cap.name}」已打回修改`
    reviewComment.value = ''
    confirmReview.value = null
    resetReviewChecks()
    await load()
  } catch (e) {
    error.value = e.message
  }
}

function askReject(cap) {
  confirmReview.value = {
    action: 'reject',
    title: '确定拒绝？',
    body: `拒绝后，「${cap.name}」将退回发布者，需修改后重新提交。`,
    okText: '拒绝',
    danger: true,
    cap
  }
}

function askReturn(cap) {
  confirmReview.value = {
    action: 'return',
    title: '确定打回？',
    body: `打回后，「${cap.name}」将回到草稿态，发布者可继续修改。`,
    okText: '打回',
    danger: false,
    cap
  }
}

async function confirmReviewOk() {
  const action = confirmReview.value
  if (!action) return
  await review(action.cap, action.action)
}

async function statusAction(cap, action) {
  try {
    await api.post(`/admin/capabilities/${cap.id}/${action}`)
    notice.value = action === 'deprecate' ? `「${cap.name}」已下架` : `「${cap.name}」已归档`
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function updateUser(user, patch) {
  try {
    await api.patch(`/admin/users/${user.id}`, patch)
    userNotice.value = patch.role ? `已将 ${user.username} 设为「${roleDefs.find((r) => r.key === patch.role)?.label}」` : patch.is_active ? `已启用 ${user.username}` : `已禁用 ${user.username}`
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function createUser() {
  error.value = ''
  userNotice.value = ''
  const f = userForm.value
  if (!f.username || !f.email || !f.password) {
    error.value = '请填写用户名、邮箱和密码'
    return
  }
  try {
    const u = await api.post('/admin/users', {
      username: f.username,
      email: f.email,
      password: f.password,
      display_name: f.display_name,
      organization: f.organization,
      role: f.role
    })
    userNotice.value = `已创建用户 ${u.username}（${roleDefs.find((r) => r.key === u.role)?.label}）`
    showCreateUser.value = false
    userForm.value = { username: '', email: '', password: '', display_name: '', organization: '', role: 'user' }
    await load()
  } catch (e) {
    error.value = e.message
  }
}

function isSelf(u) {
  return authState.user?.id === u.id
}

function openEditUser(u) {
  editUser.value = u
  editForm.value = {
    username: u.username,
    display_name: u.display_name || '',
    email: u.email || '',
    organization: u.organization || '',
    password: ''
  }
  showEditUser.value = true
}

async function saveEditUser() {
  error.value = ''
  userNotice.value = ''
  const f = editForm.value
  const patch = {}
  if (f.username && f.username !== editUser.value.username) patch.username = f.username
  if (f.display_name !== (editUser.value.display_name || '')) patch.display_name = f.display_name
  if (f.email !== (editUser.value.email || '')) patch.email = f.email
  if (f.organization !== (editUser.value.organization || '')) patch.organization = f.organization
  if (f.password) patch.password = f.password
  if (Object.keys(patch).length === 0) {
    showEditUser.value = false
    return
  }
  try {
    const u = await api.patch(`/admin/users/${editUser.value.id}`, patch)
    userNotice.value = `已更新用户 ${u.username}${patch.password ? '，密码已重置' : ''}`
    showEditUser.value = false
    await load()
  } catch (e) {
    error.value = e.message
  }
}

const confirmAction = ref(null)

async function removeUser(u) {
  error.value = ''
  userNotice.value = ''
  confirmAction.value = {
    kind: 'remove-user',
    title: '确认删除用户？',
    body: `删除「${u.username}」后，其发布的能力将转移给当前管理员。此操作不可恢复。`,
    okText: '删除',
    danger: true,
    payload: u
  }
}

async function doRemoveUser(u) {
  try {
    const r = await api.delete(`/admin/users/${u.id}`)
    userNotice.value = r.message
    await load()
  } catch (e) {
    error.value = e.message
  }
}

function openGatewayCreate() {
  gatewayForm.value = {
    id: '',
    name: '',
    description: '',
    transport: 'stdio',
    url: '',
    headers: '{}',
    command: '',
    args: '[]',
    env: '{}',
    cwd: '',
    api_token: '',
    capability_id: '',
    enabled: true
  }
  gatewayNotice.value = ''
  showGatewayModal.value = true
}

function openGatewayEdit(s) {
  gatewayForm.value = {
    id: s.id,
    name: s.name,
    description: s.description || '',
    transport: s.transport,
    url: s.url || '',
    headers: JSON.stringify(s.headers || {}, null, 2),
    command: s.command || '',
    args: JSON.stringify(s.args || [], null, 2),
    env: JSON.stringify(s.env || {}, null, 2),
    cwd: s.cwd || '',
    api_token: s.api_token || '',
    capability_id: s.capability_id || '',
    enabled: s.enabled
  }
  gatewayNotice.value = ''
  showGatewayModal.value = true
}

async function saveGateway() {
  error.value = ''
  gatewayNotice.value = ''
  const f = gatewayForm.value
  let args = []
  let env = {}
  let headers = {}
  try {
    args = JSON.parse(f.args || '[]')
    env = JSON.parse(f.env || '{}')
    headers = JSON.parse(f.headers || '{}')
  } catch (e) {
    error.value = `JSON 格式不合法：${e.message}`
    return
  }
  if (!f.name.trim()) {
    error.value = '请填写服务名称'
    return
  }
  if (f.transport === 'stdio' && !f.command.trim()) {
    error.value = 'stdio 传输需要填写启动命令'
    return
  }
  if (f.transport !== 'stdio' && !f.url.trim()) {
    error.value = 'HTTP/SSE 传输需要填写服务地址'
    return
  }
  const payload = {
    name: f.name.trim(),
    description: f.description,
    transport: f.transport,
    url: f.url.trim(),
    headers,
    command: f.command.trim(),
    args,
    env,
    cwd: f.cwd.trim(),
    api_token: f.api_token,
    capability_id: f.capability_id || '',
    enabled: f.enabled
  }
  try {
    if (f.id) {
      await api.put(`/admin/mcp-gateway/servers/${f.id}`, payload)
    } else {
      await api.post('/admin/mcp-gateway/servers', payload)
    }
    gatewayNotice.value = f.id ? '网关服务已更新' : '网关服务已创建'
    showGatewayModal.value = false
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function removeGateway(s) {
  gatewayNotice.value = ''
  confirmAction.value = {
    kind: 'remove-gateway',
    title: '确认删除网关？',
    body: `删除「${s.name}」后，外部调用端点将立即失效。`,
    okText: '删除',
    danger: true,
    payload: s
  }
}

async function doRemoveGateway(s) {
  try {
    const r = await api.delete(`/admin/mcp-gateway/servers/${s.id}`)
    gatewayNotice.value = r.message
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function confirmActionOk() {
  const action = confirmAction.value
  confirmAction.value = null
  if (!action) return
  if (action.kind === 'remove-user') await doRemoveUser(action.payload)
  if (action.kind === 'remove-gateway') await doRemoveGateway(action.payload)
  if (action.kind === 'revoke-token') await doRevokeToken(action.payload)
}

async function testGateway(s) {
  gatewayTest.value = { ...gatewayTest.value, [s.id]: { loading: true } }
  try {
    const r = await api.post(`/admin/mcp-gateway/servers/${s.id}/test`)
    gatewayTest.value = { ...gatewayTest.value, [s.id]: r }
  } catch (e) {
    gatewayTest.value = { ...gatewayTest.value, [s.id]: { connected: false, error: e.message } }
  }
}

const mcpCapabilities = computed(() =>
  [...(allCaps.value || [])]
    .filter((c) => c.type === 'mcp')
    .sort((a, b) => `${a.name}@${a.version}`.localeCompare(`${b.name}@${b.version}`))
)

function gatewayBoundCap(s) {
  const id = s?.capability_id || ''
  if (!id) return null
  return (allCaps.value || []).find((c) => c.id === id) || null
}

function gatewayBindingLabel(s) {
  const cap = gatewayBoundCap(s)
  if (cap) return `${cap.name}@${cap.version}`
  return s?.capability_id ? `${s.capability_id.slice(0, 8)}…` : '—'
}

function gatewayUrl(s) {
  return `${window.location.origin}${__API_BASE__}/mcp-gateway/${s.name}/stream`
}

function gatewaySseUrl(s) {
  return `${window.location.origin}${__API_BASE__}/mcp-gateway/${s.name}/sse`
}

function gatewayPreviewUrl(name) {
  return `${window.location.origin}${__API_BASE__}/mcp-gateway/${name || '…'}/stream`
}

function copyText(text, noticeRef) {
  navigator.clipboard?.writeText(text).then(() => {
    const msg = '已复制到剪贴板'
    if (noticeRef === 'token') tokenNotice.value = msg
    else gatewayNotice.value = msg
  })
}

function openTokenCreate() {
  tokenForm.value = {
    name: '',
    scopes: ['runtime', 'gateway', 'sync'],
    expires_days: 365
  }
  error.value = ''
  tokenNotice.value = ''
  createdTokenPlain.value = ''
  showCreatedToken.value = false
  showTokenModal.value = true
}

function toggleTokenScope(key) {
  const cur = tokenForm.value.scopes
  if (cur.includes(key)) {
    tokenForm.value.scopes = cur.filter((s) => s !== key)
  } else {
    tokenForm.value.scopes = [...cur, key]
  }
}

async function createServiceToken() {
  error.value = ''
  tokenNotice.value = ''
  const f = tokenForm.value
  if (!f.name.trim()) {
    error.value = '请填写令牌名称'
    return
  }
  if (!f.scopes.length) {
    error.value = '至少选择一个 scope'
    return
  }
  const days = Number(f.expires_days)
  try {
    const r = await api.post('/admin/service-tokens', {
      name: f.name.trim(),
      scopes: f.scopes,
      expires_days: Number.isFinite(days) && days > 0 ? days : null
    })
    createdTokenPlain.value = r.token || ''
    showTokenModal.value = false
    showCreatedToken.value = true
    tokenNotice.value = '令牌已创建；明文仅此一次，请立即复制保存'
    await load()
  } catch (e) {
    error.value = e.message
  }
}

function revokeToken(row) {
  tokenNotice.value = ''
  confirmAction.value = {
    kind: 'revoke-token',
    title: '确认吊销服务令牌？',
    body: `吊销「${row.name}」（${row.token_prefix}…）后，使用该令牌的调用将立即 401。`,
    okText: '吊销',
    danger: true,
    payload: row
  }
}

async function doRevokeToken(row) {
  try {
    await api.post(`/admin/service-tokens/${row.id}/revoke`)
    tokenNotice.value = `已吊销「${row.name}」`
    await load()
  } catch (e) {
    error.value = e.message
  }
}

const activeServiceTokens = computed(() => serviceTokens.value.filter((t) => !t.revoked))
const revokedServiceTokens = computed(() => serviceTokens.value.filter((t) => t.revoked))

const debugCaps = computed(() =>
  allCaps.value.filter((c) => c.status === 'published' && c.type === debugType.value)
)

function selectDebugType(type) {
  debugType.value = type
  debugName.value = ''
}

function openDebug() {
  if (!debugName.value) return
  debugCap.value = debugCaps.value.find((c) => c.name === debugName.value) || null
  showTrial.value = false
}

onMounted(() => {
  resetReviewChecks()
  if (route.query.trial === '1') showTrial.value = true
  load()
})

watch(
  () => route.query.trial,
  (v) => {
    if (v === '1') showTrial.value = true
  }
)
</script>

<template>
  <div class="admin-page">
      <div class="admin-header">
        <div>
          <h2 style="margin: 0">{{ sectionTitle }}</h2>
          <div class="muted" style="font-size: 13px">{{ sectionHint }}</div>
        </div>
        <div class="header-right">
          <div v-if="stats && (section === 'review' || section === 'listed')" class="header-chips">
            <div class="chip"><span class="chip-num">{{ stats.total_capabilities }}</span>能力总数</div>
            <div class="chip"><span class="chip-num success">{{ stats.published_count }}</span>已上架</div>
            <div class="chip"><span class="chip-num warning">{{ stats.reviewing_count }}</span>待审核</div>
            <div class="chip"><span class="chip-num primary">{{ stats.total_usage }}</span>总用量</div>
          </div>
          <button
            v-if="section === 'review' && stats"
            class="btn"
            type="button"
            @click="showStatsDetail = !showStatsDetail"
          >{{ showStatsDetail ? '收起统计' : '统计明细' }}</button>
          <AdminTrialPanel
            v-model:show="showTrial"
            v-model:debug-type="debugType"
            v-model:debug-name="debugName"
            :debug-caps="debugCaps"
            @open="openDebug"
            @dismiss="showTrial = false"
            @update:debug-type="selectDebugType"
          />
        </div>
      </div>

      <div v-if="notice" class="alert alert-success mb-12">{{ notice }}</div>
      <div v-if="error" class="alert alert-error mb-12">{{ error }}</div>

      <!-- 审核队列 -->
      <section v-if="section === 'review'" class="desk">
        <div v-if="showStatsDetail && stats" class="stats-band">
          <div class="panel">
            <h3>类型分布</h3>
            <div v-for="t in Object.keys(stats.type_breakdown)" :key="t" class="type-bar">
              <span style="width: 110px">{{ TYPE_LABELS[t] }}</span>
              <div class="bar"><div class="bar-fill" :style="{ width: Math.min(100, stats.type_breakdown[t] / stats.total_capabilities * 100) + '%' }"></div></div>
              <span class="muted">{{ stats.type_breakdown[t] }}</span>
            </div>
          </div>
          <div class="panel">
            <h3>热门能力 TOP5</h3>
            <div v-for="item in stats.top_used" :key="item.id" class="top-item">
              <router-link :to="`/capabilities/${item.id}`">{{ item.name }}</router-link>
              <span class="badge">{{ item.type }}</span>
              <span class="muted">{{ item.count }} 次</span>
            </div>
            <div v-if="!(stats.top_used || []).length" class="muted" style="font-size: 13px">暂无用量</div>
          </div>
          <div class="panel">
            <h3>最近使用</h3>
            <div v-for="(item, i) in stats.recent_usage" :key="i" class="recent-item">
              <div>{{ item.capability }}</div>
              <div class="muted" style="font-size: 12px">{{ item.action }} · {{ formatDate(item.at) }}</div>
            </div>
            <div v-if="!(stats.recent_usage || []).length" class="muted" style="font-size: 13px">暂无记录</div>
          </div>
        </div>

        <div class="desk-toolbar">
          <div class="seg">
            <button type="button" class="seg-item" :class="{ active: auditFilter === 'pending' }" @click="setAuditFilter('pending')">
              待审核 <span>{{ auditCounts.pending }}</span>
            </button>
            <button type="button" class="seg-item" :class="{ active: auditFilter === 'rejected' }" @click="setAuditFilter('rejected')">
              已拒绝 <span>{{ auditCounts.rejected }}</span>
            </button>
            <button type="button" class="seg-item" :class="{ active: auditFilter === 'returned' }" @click="setAuditFilter('returned')">
              已打回 <span>{{ auditCounts.returned }}</span>
            </button>
          </div>
          <select v-model="auditTypeFilter" class="select" style="max-width: 180px">
            <option value="">全部类型</option>
            <option v-for="t in Object.keys(TYPE_LABELS)" :key="t" :value="t">{{ TYPE_LABELS[t] }}</option>
          </select>
        </div>

        <div class="audit-split">
            <div class="audit-list panel">
              <div class="list-head">
                <span>能力</span>
                <span>风险程度</span>
              </div>
              <div v-if="auditList.length === 0" class="empty-sm">暂无记录</div>
              <button
                v-for="cap in auditList"
                :key="cap.id"
                type="button"
                class="list-row"
                :class="{ active: selectedId === cap.id }"
                @click="selectCap(cap)"
              >
                <div class="skill-cell">
                  <div class="skill-icon" :style="{ background: typeColor(cap.type) }">{{ typeInitial(cap.type) }}</div>
                  <div class="skill-meta">
                    <div class="skill-name-row">
                      <span class="skill-name">{{ cap.name }}</span>
                      <span class="ver-tag">v{{ cap.version }}</span>
                    </div>
                    <div class="skill-desc muted">{{ cap.description || TYPE_LABELS[cap.type] }}</div>
                  </div>
                </div>
                <span class="risk" :class="riskOf(cap).key">{{ riskOf(cap).label }}</span>
              </button>
            </div>

            <div class="audit-detail panel">
              <div v-if="!selectedCap" class="empty-sm">从左侧选择一条能力</div>
              <template v-else>
                <div class="detail-top">
                  <div class="detail-identity">
                    <div class="skill-icon lg" :style="{ background: typeColor(selectedCap.type) }">{{ typeInitial(selectedCap.type) }}</div>
                    <div>
                      <div class="detail-name-row">
                        <h3 class="detail-name">{{ selectedCap.name }}</h3>
                        <StatusBadge :status="selectedCap.status" />
                      </div>
                      <div class="muted" style="font-size: 12px; margin-top: 4px">
                        {{ TYPE_LABELS[selectedCap.type] }}
                        <span v-if="shelfLabel(selectedCap.type)"> · {{ shelfLabel(selectedCap.type) }}</span>
                      </div>
                    </div>
                  </div>
                  <div v-if="selectedCap.status === 'reviewing'" class="detail-actions">
                    <button class="btn btn-danger" type="button" @click="askReject(selectedCap)">拒绝</button>
                    <button class="btn" type="button" @click="askReturn(selectedCap)">打回</button>
                    <button
                      class="btn btn-primary"
                      type="button"
                      :disabled="!allReviewChecked"
                      @click="review(selectedCap, 'approve')"
                    >通过</button>
                  </div>
                </div>

                <div class="meta-row">
                  <div><span class="meta-k">开发者</span>{{ selectedCap.author_name || '—' }}</div>
                  <div><span class="meta-k">来源</span>{{ selectedCap.organization || '个人' }}</div>
                  <div><span class="meta-k">加入次数</span>{{ selectedCap.usage_count || 0 }}</div>
                  <div><span class="meta-k">提交时间</span>{{ formatDate(selectedCap.updated_at) }}</div>
                  <div><span class="meta-k">版本</span>v{{ selectedCap.version }}</div>
                </div>

                <div class="vis-block">
                  <span class="meta-k">可见范围</span>
                  <span class="vis-pill">{{ VISIBILITY_LABELS[selectedCap.visibility] || selectedCap.visibility }}</span>
                </div>

                <div v-if="selectedCap.status === 'reviewing'" class="checklist-box">
                  <div class="check-title">审核清单</div>
                  <label v-for="(item, i) in REVIEW_CHECKLIST" :key="i" class="check-item">
                    <input v-model="reviewChecks[i]" type="checkbox" />
                    <span>{{ item }}</span>
                  </label>
                  <textarea
                    v-model="reviewComment"
                    class="textarea"
                    rows="2"
                    placeholder="审核意见（可选）"
                  ></textarea>
                  <div v-if="!allReviewChecked" class="muted" style="font-size: 12px">勾选全部清单后可点「通过」</div>
                </div>

                <div class="detail-tabs">
                  <button type="button" class="detail-tab" :class="{ active: detailTab === 'intro' }" @click="detailTab = 'intro'">能力介绍</button>
                  <button type="button" class="detail-tab" :class="{ active: detailTab === 'files' }" @click="detailTab = 'files'">文件预览</button>
                  <button type="button" class="detail-tab" :class="{ active: detailTab === 'report' }" @click="detailTab = 'report'">校验报告</button>
                </div>

                <div v-if="detailTab === 'intro'" class="detail-body">
                  <p v-if="selectedCap.description" class="desc">{{ selectedCap.description }}</p>
                  <div v-if="selectedReadmeHtml" class="readme-body" v-html="selectedReadmeHtml"></div>
                  <div v-else class="muted" style="font-size: 13px">暂无 README</div>
                  <router-link class="op-link" :to="`/capabilities/${selectedCap.id}`">打开完整详情 →</router-link>
                </div>

                <div v-else-if="detailTab === 'files'" class="detail-body">
                  <PackagePreview :capability-id="selectedCap.id" compact />
                </div>

                <div v-else class="detail-body">
                  <template v-if="selectedValidation && ((selectedValidation.errors || []).length || (selectedValidation.warnings || []).length || fileList.length)">
                    <div v-for="(e, i) in (selectedValidation.errors || [])" :key="'e'+i" class="vr-line err">{{ e }}</div>
                    <div v-for="(w, i) in (selectedValidation.warnings || [])" :key="'w'+i" class="vr-line warn">{{ w }}</div>
                    <div v-if="fileList.length" class="file-tree">
                      <div class="file-tree-title">包内文件（点击上方「文件预览」查看内容）</div>
                      <button
                        v-for="(f, i) in fileList.slice(0, 40)"
                        :key="i"
                        type="button"
                        class="file-line-btn"
                        @click="detailTab = 'files'"
                      >{{ f }}</button>
                      <div v-if="fileList.length > 40" class="muted" style="font-size: 12px">…共 {{ fileList.length }} 个</div>
                    </div>
                    <div v-if="selectedValidation.ok !== false" class="muted" style="font-size: 12px; margin-top: 8px">结构校验通过</div>
                  </template>
                  <div v-else class="muted" style="font-size: 13px">暂无校验报告</div>
                </div>
              </template>
            </div>
          </div>
      </section>

      <section v-else-if="section === 'listed'">
        <div class="desk-toolbar">
          <div class="seg">
            <button type="button" class="seg-item" :class="{ active: listedFilter === 'published' }" @click="listedFilter = 'published'">
              已上架 <span>{{ listedCounts.published }}</span>
            </button>
            <button type="button" class="seg-item" :class="{ active: listedFilter === 'deprecated' }" @click="listedFilter = 'deprecated'">
              已下架 <span>{{ listedCounts.deprecated }}</span>
            </button>
            <button type="button" class="seg-item" :class="{ active: listedFilter === 'all' }" @click="listedFilter = 'all'">
              全部 <span>{{ listedCounts.all }}</span>
            </button>
          </div>
        </div>
        <div class="panel table-panel">
          <div v-if="listedCaps.length === 0" class="empty">暂无记录</div>
          <table v-else class="skill-table">
            <thead>
              <tr>
                <th style="width: 36%">能力</th>
                <th>状态</th>
                <th>可见范围</th>
                <th>作者</th>
                <th>使用量</th>
                <th style="width: 160px">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="cap in listedCaps" :key="cap.id">
                <td>
                  <div class="skill-cell">
                    <div class="skill-icon" :style="{ background: typeColor(cap.type) }">{{ typeInitial(cap.type) }}</div>
                    <div class="skill-meta">
                      <div class="skill-name-row">
                        <router-link class="skill-name link" :to="`/capabilities/${cap.id}`">{{ cap.name }}</router-link>
                        <span class="ver-tag">v{{ cap.version }}</span>
                      </div>
                      <div class="skill-desc muted">{{ cap.description || TYPE_LABELS[cap.type] }}</div>
                    </div>
                  </div>
                </td>
                <td><StatusBadge :status="cap.status" /></td>
                <td><span class="vis-pill">{{ VISIBILITY_LABELS[cap.visibility] || cap.visibility }}</span></td>
                <td>{{ cap.author_name }}</td>
                <td>{{ cap.usage_count }}</td>
                <td>
                  <div class="ops">
                    <router-link class="op-link" :to="`/capabilities/${cap.id}`">详情</router-link>
                    <button v-if="cap.status === 'published'" class="op-link danger" type="button" @click="statusAction(cap, 'deprecate')">下架</button>
                    <button
                      v-if="['published', 'deprecated'].includes(cap.status)"
                      class="op-link danger"
                      type="button"
                      @click="statusAction(cap, 'archive')"
                    >归档</button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section v-else-if="section === 'users'" class="panel">
        <div class="users-head">
          <p class="muted boundary-hint">
            市场是目录控制面。宿主 BuiltinTool（src/tools）与通道插件（钉钉/飞书）不上架。
          </p>
          <div class="role-cards">
            <div v-for="r in roleDefs" :key="r.key" class="role-card">
              <strong>{{ r.label }}</strong>
              <span class="muted" style="font-size: 12px">{{ r.desc }}</span>
            </div>
          </div>
          <div class="users-tools">
            <input v-model="userQuery" class="input" style="max-width: 220px" placeholder="搜索用户…" />
            <button class="btn btn-primary" @click="showCreateUser = true">+ 新增用户</button>
          </div>
        </div>
        <div v-if="userNotice" class="alert alert-success">{{ userNotice }}</div>
        <table class="table mt-16">
          <thead>
            <tr><th>用户</th><th>邮箱</th><th>组织</th><th>角色</th><th>状态</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-if="filteredUsers.length === 0">
              <td colspan="6" class="muted">{{ userQuery ? '无匹配用户' : '暂无用户' }}</td>
            </tr>
            <tr v-for="u in filteredUsers" :key="u.id">
              <td>{{ u.display_name || u.username }} <span class="muted">@{{ u.username }}</span></td>
              <td>{{ u.email }}</td>
              <td>{{ u.organization || u.team || '-' }}</td>
              <td>
                <select :value="u.role" class="select" style="width: auto; padding: 4px 8px" :disabled="isSelf(u)" @change="updateUser(u, { role: $event.target.value })">
                  <option value="admin">管理员</option>
                  <option value="publisher">发布者</option>
                  <option value="user">普通用户</option>
                </select>
              </td>
              <td>
                <span class="badge" :class="u.is_active ? 'badge-success' : 'badge-danger'">{{ u.is_active ? '正常' : '禁用' }}</span>
                <span v-if="isSelf(u)" class="muted" style="font-size: 11px">（自己，不可禁用/降级）</span>
              </td>
              <td>
                <div class="flex" style="gap: 6px">
                  <button class="btn btn-sm" @click="openEditUser(u)">编辑</button>
                  <button v-if="!isSelf(u)" class="btn btn-sm" :class="u.is_active ? '' : 'btn-success'" @click="updateUser(u, { is_active: !u.is_active })">{{ u.is_active ? '禁用' : '启用' }}</button>
                  <button v-if="!isSelf(u)" class="btn btn-sm btn-danger" @click="removeUser(u)">删除</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      <section v-else-if="section === 'gateway'">
        <div class="flex-between mb-16" style="align-items: flex-end">
          <p class="muted" style="font-size: 13px; max-width: 720px; margin: 0">
            外部 MCP 客户端（Dify、Claude Desktop、其他 Agent）用令牌接入；
            市场 MCP 能力包也可以用 transport=gateway 引用这里的服务。
          </p>
          <button class="btn btn-primary" @click="openGatewayCreate">+ 注册 MCP 服务</button>
        </div>
        <div v-if="gatewayNotice" class="alert alert-success">{{ gatewayNotice }}</div>
        <div v-if="gatewayServers.length === 0" class="panel empty">
          还没有注册 MCP 服务。点击右上角「+ 注册 MCP 服务」。
        </div>
        <div v-else class="gw-grid">
          <div v-for="s in gatewayServers" :key="s.id" class="gw-card panel">
            <div class="gw-card-top">
              <div>
                <strong>{{ s.name }}</strong>
                <div class="muted" style="font-size: 12px; margin-top: 2px">{{ s.description || '无描述' }}</div>
              </div>
              <div class="gw-badges">
                <span class="badge badge-primary">{{ { stdio: 'stdio', http: 'HTTP', sse: 'SSE' }[s.transport] || s.transport }}</span>
                <span class="badge" :class="s.enabled ? 'badge-success' : 'badge-danger'">{{ s.enabled ? '启用' : '停用' }}</span>
                <span v-if="s.api_token" class="badge badge-warning">令牌</span>
                <span v-else class="badge">免鉴权</span>
              </div>
            </div>
            <code class="gw-code">{{ s.transport === 'stdio' ? `${s.command} ${(s.args || []).join(' ')}` : s.url }}</code>
            <div class="gw-binding">绑定能力：{{ gatewayBindingLabel(s) }}</div>
            <div class="gw-card-actions">
              <button class="btn btn-sm" @click="copyText(gatewayUrl(s))">复制 Stream</button>
              <button class="btn btn-sm" @click="copyText(gatewaySseUrl(s))">复制 SSE</button>
              <button class="btn btn-sm" @click="testGateway(s)">测试连接</button>
              <button class="btn btn-sm" @click="openGatewayEdit(s)">编辑</button>
              <button class="btn btn-sm btn-danger" @click="removeGateway(s)">删除</button>
            </div>
            <div v-if="gatewayTest[s.id]" class="gw-test">
              <div v-if="gatewayTest[s.id].loading" class="muted" style="font-size: 12px">连接测试中…</div>
              <div v-else>
                <span class="badge" :class="gatewayTest[s.id].connected ? 'badge-success' : 'badge-danger'">
                  {{ gatewayTest[s.id].connected ? `已连接，发现 ${gatewayTest[s.id].tools.length} 个工具` : '连接失败' }}
                </span>
                <div class="flex" style="gap: 6px; flex-wrap: wrap; margin-top: 8px">
                  <span v-for="t in (gatewayTest[s.id].tools || [])" :key="t.name" class="badge badge-primary">{{ t.name }}</span>
                </div>
                <div v-if="gatewayTest[s.id].error" class="muted" style="font-size: 12px; margin-top: 6px">{{ gatewayTest[s.id].error }}</div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section v-else-if="section === 'tokens'">
        <div class="flex-between mb-16" style="align-items: flex-end">
          <p class="muted" style="font-size: 13px; max-width: 720px; margin: 0">
            服务令牌用于 Agent / CI / dashboard 以
            <code>Authorization: Bearer mkt_svc_…</code>
            调用 runtime、能力网关与目录同步。明文仅在创建时展示一次。
          </p>
          <button class="btn btn-primary" type="button" @click="openTokenCreate">+ 签发令牌</button>
        </div>
        <div v-if="tokenNotice" class="alert alert-success mb-12">{{ tokenNotice }}</div>

        <div v-if="activeServiceTokens.length === 0 && revokedServiceTokens.length === 0" class="panel empty">
          还没有服务令牌。点击右上角「+ 签发令牌」。
        </div>

        <template v-else>
          <h3 class="token-h">有效令牌（{{ activeServiceTokens.length }}）</h3>
          <table v-if="activeServiceTokens.length" class="table">
            <thead>
              <tr>
                <th>名称</th>
                <th>前缀</th>
                <th>绑定账号</th>
                <th>Scopes</th>
                <th>过期</th>
                <th>最近使用</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="t in activeServiceTokens" :key="t.id">
                <td><strong>{{ t.name }}</strong></td>
                <td><code class="token-prefix">{{ t.token_prefix }}…</code></td>
                <td>{{ t.username || t.user_id }}</td>
                <td>
                  <div class="token-scopes">
                    <span v-for="s in (t.scopes || [])" :key="s" class="badge badge-primary">{{ s }}</span>
                  </div>
                </td>
                <td class="muted" style="font-size: 12px">{{ t.expires_at ? formatDate(t.expires_at) : '永不过期' }}</td>
                <td class="muted" style="font-size: 12px">{{ t.last_used_at ? formatDate(t.last_used_at) : '—' }}</td>
                <td>
                  <button class="btn btn-sm btn-danger" type="button" @click="revokeToken(t)">吊销</button>
                </td>
              </tr>
            </tbody>
          </table>
          <div v-else class="muted mb-16" style="font-size: 13px">当前无有效令牌</div>

          <template v-if="revokedServiceTokens.length">
            <h3 class="token-h muted">已吊销（{{ revokedServiceTokens.length }}）</h3>
            <table class="table table-muted">
              <thead>
                <tr>
                  <th>名称</th>
                  <th>前缀</th>
                  <th>绑定账号</th>
                  <th>Scopes</th>
                  <th>创建时间</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="t in revokedServiceTokens" :key="t.id">
                  <td>{{ t.name }}</td>
                  <td><code class="token-prefix">{{ t.token_prefix }}…</code></td>
                  <td>{{ t.username || t.user_id }}</td>
                  <td>
                    <span v-for="s in (t.scopes || [])" :key="s" class="badge">{{ s }}</span>
                  </td>
                  <td class="muted" style="font-size: 12px">{{ formatDate(t.created_at) }}</td>
                </tr>
              </tbody>
            </table>
          </template>
        </template>
      </section>

    <ConfirmActionModal
      :show="!!confirmAction"
      :title="confirmAction?.title || ''"
      :body="confirmAction?.body || ''"
      :ok-text="confirmAction?.okText || '确定'"
      :danger="!!confirmAction?.danger"
      @ok="confirmActionOk"
      @cancel="confirmAction = null"
    />
    <DebugCapabilityModal :show="!!debugCap" :cap="debugCap" title="云端试用" @close="debugCap = null" />

    <div v-if="confirmReview" class="confirm-mask" @click.self="confirmReview = null">
      <div class="confirm-card">
        <div class="confirm-title">
          <span class="confirm-warn">!</span>
          {{ confirmReview.title }}
        </div>
        <p class="confirm-body muted">{{ confirmReview.body }}</p>
        <textarea
          v-model="reviewComment"
          class="textarea"
          rows="2"
          placeholder="审核意见（可选）"
          style="margin-top: 12px"
        ></textarea>
        <div class="confirm-actions">
          <button class="btn" type="button" @click="confirmReview = null">取消</button>
          <button
            class="btn"
            :class="confirmReview.danger ? 'btn-danger' : 'btn-primary'"
            type="button"
            @click="confirmReviewOk"
          >{{ confirmReview.okText }}</button>
        </div>
      </div>
    </div>

    <div v-if="showCreateUser" class="modal-mask" @click.self="showCreateUser = false">
      <div class="modal panel">
        <div class="modal-header">
          <h3 style="margin: 0">新增用户</h3>
          <button class="modal-close" @click="showCreateUser = false">✕</button>
        </div>
        <div class="grid" style="grid-template-columns: 1fr 1fr">
          <div class="field"><label>用户名 *</label><input v-model="userForm.username" class="input" placeholder="3-64 位字母数字/._-" /></div>
          <div class="field"><label>邮箱 *</label><input v-model="userForm.email" class="input" placeholder="user@example.com" /></div>
          <div class="field"><label>初始密码 *</label><input v-model="userForm.password" type="password" class="input" placeholder="至少 6 位" /></div>
          <div class="field"><label>显示名</label><input v-model="userForm.display_name" class="input" /></div>
          <div class="field"><label>组织</label><input v-model="userForm.organization" class="input" /></div>
        </div>
        <div class="field mt-12">
          <label>角色</label>
          <select v-model="userForm.role" class="select">
            <option v-for="r in roleDefs" :key="r.key" :value="r.key">{{ r.label }}：{{ r.desc }}</option>
          </select>
        </div>
        <div v-if="error" class="alert alert-error mt-12">{{ error }}</div>
        <div class="modal-foot">
          <span class="muted" style="font-size: 12px">创建后用户可用账号密码登录</span>
          <div class="flex" style="gap: 10px">
            <button class="btn" @click="showCreateUser = false">取消</button>
            <button class="btn btn-primary" @click="createUser">创建</button>
          </div>
        </div>
      </div>
    </div>

    <div v-if="showEditUser && editUser" class="modal-mask" @click.self="showEditUser = false">
      <div class="modal panel">
        <div class="modal-header">
          <h3 style="margin: 0">编辑用户：{{ editUser.username }}</h3>
          <button class="modal-close" @click="showEditUser = false">✕</button>
        </div>
        <div class="grid" style="grid-template-columns: 1fr 1fr">
          <div class="field"><label>用户名</label><input v-model="editForm.username" class="input" /></div>
          <div class="field"><label>邮箱</label><input v-model="editForm.email" class="input" /></div>
          <div class="field"><label>显示名</label><input v-model="editForm.display_name" class="input" /></div>
          <div class="field"><label>组织</label><input v-model="editForm.organization" class="input" /></div>
          <div class="field"><label>重置密码（留空不改）</label><input v-model="editForm.password" type="password" class="input" placeholder="至少 6 位" /></div>
        </div>
        <div v-if="error" class="alert alert-error mt-12">{{ error }}</div>
        <div class="modal-foot">
          <span class="muted" style="font-size: 12px">角色与启停直接在表格中操作；自己不能禁用/降级</span>
          <div class="flex" style="gap: 10px">
            <button class="btn" @click="showEditUser = false">取消</button>
            <button class="btn btn-primary" @click="saveEditUser">保存</button>
          </div>
        </div>
      </div>
    </div>

    <div v-if="showGatewayModal" class="modal-mask" @click.self="showGatewayModal = false">
      <div class="modal panel gw-modal">
        <div class="modal-header">
          <h3 style="margin: 0">{{ gatewayForm.id ? '编辑 MCP 服务' : '注册 MCP 服务' }}</h3>
          <button class="modal-close" @click="showGatewayModal = false">✕</button>
        </div>

        <div class="grid" style="grid-template-columns: 1fr 1fr">
          <div class="field">
            <label>服务名称 *（字母数字/_-，外部端点路径）</label>
            <input v-model="gatewayForm.name" class="input" placeholder="如：mysql-query" />
          </div>
          <div class="field">
            <label>传输方式</label>
            <select v-model="gatewayForm.transport" class="select">
              <option value="stdio">stdio（本地子进程，自动转 HTTP）</option>
              <option value="http">HTTP / Streamable HTTP（远程）</option>
              <option value="sse">SSE（远程）</option>
            </select>
          </div>
        </div>

        <div class="field">
          <label>描述</label>
          <input v-model="gatewayForm.description" class="input" placeholder="这个 MCP 服务提供什么能力" />
        </div>

        <div class="field">
          <label>绑定能力（可选，仅 mcp 类型）</label>
          <select v-model="gatewayForm.capability_id" class="select">
            <option value="">不绑定</option>
            <option v-for="c in mcpCapabilities" :key="c.id" :value="c.id">
              {{ c.name }}@{{ c.version }}
            </option>
          </select>
          <div class="muted" style="font-size: 12px; margin-top: 4px">
            绑定后网关调用按该能力计审计；一个能力只能绑定一个网关服务。
          </div>
        </div>

        <template v-if="gatewayForm.transport === 'stdio'">
          <div class="grid" style="grid-template-columns: 2fr 1fr">
            <div class="field">
              <label>启动命令 *</label>
              <input v-model="gatewayForm.command" class="input" placeholder="如：python / uvx / node" />
            </div>
            <div class="field">
              <label>工作目录</label>
              <input v-model="gatewayForm.cwd" class="input" placeholder="可选" />
            </div>
          </div>
          <div class="field">
            <label>启动参数（JSON 数组，支持 ${ENV} 占位）</label>
            <textarea v-model="gatewayForm.args" class="textarea code" rows="2" spellcheck="false"></textarea>
          </div>
          <div class="field">
            <label>环境变量（JSON 对象，支持 ${ENV:default}）</label>
            <textarea v-model="gatewayForm.env" class="textarea code" rows="3" spellcheck="false"></textarea>
          </div>
        </template>

        <template v-else>
          <div class="field">
            <label>服务地址 *</label>
            <input v-model="gatewayForm.url" class="input" placeholder="如：https://mcp.example.com/mcp" />
          </div>
          <div class="field">
            <label>请求头（JSON 对象，支持 ${ENV} 占位，如 Authorization）</label>
            <textarea v-model="gatewayForm.headers" class="textarea code" rows="3" spellcheck="false"></textarea>
          </div>
        </template>

        <div class="grid" style="grid-template-columns: 1fr 1fr">
          <div class="field">
            <label>外部调用令牌（留空 = 内部免鉴权）</label>
            <input v-model="gatewayForm.api_token" class="input" placeholder="外部客户端需带 X-Gateway-Token" />
          </div>
          <div class="field">
            <label>启用</label>
            <label class="checkbox">
              <input v-model="gatewayForm.enabled" type="checkbox" />
              对外提供该网关端点
            </label>
          </div>
        </div>

        <div v-if="error" class="alert alert-error mt-12">{{ error }}</div>
        <div class="modal-foot">
          <span class="muted" style="font-size: 12px">
            外部连接：{{ gatewayPreviewUrl(gatewayForm.name) }}
          </span>
          <div class="flex" style="gap: 10px">
            <button class="btn" @click="showGatewayModal = false">取消</button>
            <button class="btn btn-primary" @click="saveGateway">保存</button>
          </div>
        </div>
      </div>
    </div>

    <div v-if="showTokenModal" class="modal-mask" @click.self="showTokenModal = false">
      <div class="modal panel" style="max-width: 520px">
        <div class="modal-header">
          <h3 style="margin: 0">签发服务令牌</h3>
          <button class="modal-close" type="button" @click="showTokenModal = false">✕</button>
        </div>
        <div class="field">
          <label>名称 *</label>
          <input v-model="tokenForm.name" class="input" placeholder="如：零号员工生产 / CI 同步" />
        </div>
        <div class="field mt-12">
          <label>Scopes *</label>
          <div class="token-scope-list">
            <label v-for="opt in TOKEN_SCOPE_OPTS" :key="opt.key" class="token-scope-item">
              <input
                type="checkbox"
                :checked="tokenForm.scopes.includes(opt.key)"
                @change="toggleTokenScope(opt.key)"
              />
              <span>
                <strong>{{ opt.label }}</strong>
                <span class="muted" style="font-size: 12px; display: block">{{ opt.desc }}</span>
              </span>
            </label>
          </div>
        </div>
        <div class="field mt-12">
          <label>有效天数（留空或 0 = 不设过期，建议 365）</label>
          <input v-model.number="tokenForm.expires_days" class="input" type="number" min="0" max="3650" />
        </div>
        <div v-if="error" class="alert alert-error mt-12">{{ error }}</div>
        <div class="modal-foot">
          <span class="muted" style="font-size: 12px">将自动创建/绑定 svc_* 服务账号</span>
          <div class="flex" style="gap: 10px">
            <button class="btn" type="button" @click="showTokenModal = false">取消</button>
            <button class="btn btn-primary" type="button" @click="createServiceToken">签发</button>
          </div>
        </div>
      </div>
    </div>

    <div v-if="showCreatedToken" class="modal-mask" @click.self="showCreatedToken = false">
      <div class="modal panel" style="max-width: 560px">
        <div class="modal-header">
          <h3 style="margin: 0">请保存令牌明文</h3>
          <button class="modal-close" type="button" @click="showCreatedToken = false">✕</button>
        </div>
        <p class="muted" style="font-size: 13px; margin: 0 0 12px">
          关闭后无法再次查看完整令牌。请复制到密钥库或桌面/Agent 配置。
        </p>
        <code class="token-plain">{{ createdTokenPlain }}</code>
        <div class="modal-foot">
          <span class="muted" style="font-size: 12px">用法：Authorization: Bearer &lt;token&gt;</span>
          <div class="flex" style="gap: 10px">
            <button class="btn btn-primary" type="button" @click="copyText(createdTokenPlain, 'token')">复制令牌</button>
            <button class="btn" type="button" @click="showCreatedToken = false">已保存，关闭</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.admin-page { min-width: 0; }
.header-right { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; justify-content: flex-end; }
.trial-wrap { position: relative; z-index: 30; }
.trial-pop {
  position: absolute; right: 0; top: calc(100% + 8px); width: 320px; z-index: 30;
  padding: 14px; box-shadow: var(--shadow-lg);
}
.trial-dismiss { position: fixed; inset: 0; z-index: 20; }
.stats-band {
  display: grid; grid-template-columns: 1.2fr 1fr 1fr; gap: 12px; margin-bottom: 14px;
}
.desk-toolbar {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  flex-wrap: wrap; margin-bottom: 14px;
}
.desk-toolbar .seg { margin-bottom: 0; }
.users-head { display: flex; flex-direction: column; gap: 12px; }
.boundary-hint { margin: 0; font-size: 13px; line-height: 1.55; }
.users-tools { display: flex; justify-content: flex-end; gap: 10px; flex-wrap: wrap; }
.gw-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 14px; }
.gw-card-top { display: flex; justify-content: space-between; gap: 10px; align-items: flex-start; margin-bottom: 10px; }
.gw-badges { display: flex; flex-wrap: wrap; gap: 6px; justify-content: flex-end; }
.gw-card .gw-code { display: block; margin: 8px 0 12px; }
.gw-binding { font-size: 12px; color: var(--muted); margin: -6px 0 10px; }
.gw-card-actions { display: flex; flex-wrap: wrap; gap: 6px; }
.gw-test { margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--border); }
.token-h { margin: 0 0 10px; font-size: 15px; }
.token-prefix { font-size: 12px; }
.token-scopes { display: flex; flex-wrap: wrap; gap: 4px; }
.token-scope-list { display: flex; flex-direction: column; gap: 8px; margin-top: 6px; }
.token-scope-item {
  display: flex; gap: 10px; align-items: flex-start;
  padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px;
  cursor: pointer;
}
.token-plain {
  display: block; width: 100%; box-sizing: border-box;
  padding: 12px; border-radius: 8px; background: var(--bg-muted, #f6f7f9);
  font-family: 'Cascadia Code', Consolas, monospace; font-size: 12px;
  word-break: break-all; white-space: pre-wrap;
}
.table-muted { opacity: 0.72; }
.gw-code {
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 11px;
  color: #2451c7;
  word-break: break-all;
}
.gw-modal { width: 680px; max-width: 100%; }
.checkbox { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--muted); padding-top: 9px; }
.code { font-family: 'Cascadia Code', Consolas, monospace; font-size: 12px; }
.admin-header {
  display: flex; justify-content: space-between; align-items: center; gap: 16px;
  flex-wrap: wrap; margin-bottom: 16px;
}
.header-chips { display: flex; gap: 10px; flex-wrap: wrap; }
.role-cards { display: flex; gap: 10px; flex-wrap: wrap; }
.role-card {
  flex: 1; min-width: 200px; background: var(--panel-2); border: 1px solid var(--border);
  border-radius: 10px; padding: 10px 14px; display: flex; flex-direction: column; gap: 4px;
}
.modal-mask {
  position: fixed; inset: 0; background: var(--overlay, rgba(15, 23, 42, 0.45)); z-index: 100;
  display: flex; align-items: center; justify-content: center; padding: 20px;
}
.modal { width: 640px; max-width: 100%; max-height: 90vh; overflow: auto; box-shadow: 0 24px 64px rgba(0,0,0,.55); }
.modal-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.modal-close { background: none; border: none; color: var(--muted); font-size: 16px; cursor: pointer; }
.field { display: flex; flex-direction: column; gap: 6px; }
.field label { font-size: 13px; color: var(--muted); }
.mt-12 { margin-top: 12px; }
.mb-12 { margin-bottom: 12px; }
.modal-foot { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-top: 20px; }
.chip {
  display: flex; flex-direction: column; align-items: center; gap: 2px;
  background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
  padding: 8px 14px; font-size: 12px; color: var(--muted);
}
.chip-num { font-size: 20px; font-weight: 700; color: var(--text); }
.chip-num.success { color: var(--success); }
.chip-num.warning { color: var(--warning); }
.chip-num.primary { color: var(--primary); }

.seg {
  display: inline-flex; background: var(--panel-2); border-radius: 10px; padding: 3px; margin-bottom: 14px; gap: 2px;
}
.seg-item {
  background: transparent; border: none; color: var(--muted); padding: 7px 14px;
  border-radius: 8px; cursor: pointer; font-size: 13px;
}
.seg-item span { margin-left: 4px; font-variant-numeric: tabular-nums; }
.seg-item.active { background: #fff; color: var(--primary); font-weight: 600; box-shadow: 0 1px 3px rgba(0,0,0,.06); }

.audit-split {
  display: grid; grid-template-columns: minmax(280px, 380px) 1fr; gap: 14px; align-items: start;
  min-height: 520px;
}
.audit-list { padding: 0; overflow: hidden; max-height: calc(100vh - 200px); overflow-y: auto; }
.list-head {
  display: flex; justify-content: space-between; padding: 10px 14px;
  font-size: 12px; color: var(--muted); background: #fafbfc; border-bottom: 1px solid var(--border);
  position: sticky; top: 0;
}
.list-row {
  width: 100%; display: flex; align-items: center; justify-content: space-between; gap: 10px;
  text-align: left; background: none; border: none; border-bottom: 1px solid var(--border);
  padding: 12px 14px; cursor: pointer;
}
.list-row:hover { background: #fafbfc; }
.list-row.active { background: #f0f4ff; }
.risk { font-size: 12px; white-space: nowrap; flex: none; }
.risk.none { color: var(--muted); }
.risk.medium { color: #c27803; }
.risk.high { color: var(--danger); }

.skill-cell { display: flex; gap: 10px; align-items: flex-start; min-width: 0; }
.skill-icon {
  width: 36px; height: 36px; border-radius: 9px; flex: none;
  color: #fff; font-weight: 700; font-size: 14px;
  display: flex; align-items: center; justify-content: center;
}
.skill-icon.lg { width: 48px; height: 48px; border-radius: 12px; font-size: 18px; }
.skill-meta { min-width: 0; }
.skill-name-row { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.skill-name { color: var(--text); font-weight: 650; font-size: 13px; }
.skill-name.link { text-decoration: none; }
.skill-name.link:hover { color: var(--primary); }
.ver-tag {
  font-size: 11px; color: var(--muted); background: var(--panel-2);
  border: 1px solid var(--border); border-radius: 6px; padding: 1px 6px;
}
.skill-desc {
  margin-top: 3px; font-size: 12px; line-height: 1.4;
  display: -webkit-box; -webkit-line-clamp: 1; line-clamp: 1; -webkit-box-orient: vertical; overflow: hidden;
}

.audit-detail { padding: 18px 20px; min-height: 520px; max-height: calc(100vh - 200px); overflow-y: auto; }
.detail-top { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; align-items: flex-start; }
.detail-identity { display: flex; gap: 12px; align-items: flex-start; }
.detail-name-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.detail-name { margin: 0; font-size: 18px; font-weight: 700; }
.detail-actions { display: flex; gap: 8px; flex-wrap: wrap; }

.meta-row {
  display: flex; flex-wrap: wrap; gap: 16px 20px; margin-top: 16px;
  padding: 12px 0; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border);
  font-size: 13px;
}
.meta-k { color: var(--muted); margin-right: 6px; font-size: 12px; }
.vis-block { display: flex; align-items: center; gap: 8px; margin-top: 12px; }
.vis-pill {
  display: inline-flex; padding: 2px 8px; border-radius: 999px;
  background: var(--panel-2); border: 1px solid var(--border); font-size: 12px;
}

.checklist-box {
  margin-top: 14px; padding: 12px; background: #fafbfc; border: 1px solid var(--border); border-radius: 10px;
}
.check-title { font-size: 13px; font-weight: 650; margin-bottom: 8px; }
.check-item {
  display: flex; gap: 8px; align-items: flex-start; font-size: 12px; color: var(--muted);
  margin: 6px 0; cursor: pointer;
}
.checklist-box .textarea { margin-top: 8px; margin-bottom: 6px; }

.detail-tabs {
  display: flex; gap: 2px; border-bottom: 1px solid var(--border); margin-top: 16px;
}
.detail-tab {
  background: none; border: none; color: var(--muted); padding: 8px 12px; cursor: pointer; font-size: 13px;
  border-bottom: 2px solid transparent;
}
.detail-tab.active { color: var(--primary); border-bottom-color: var(--primary); font-weight: 600; }
.detail-body { padding-top: 14px; }
.desc { margin: 0 0 12px; font-size: 13px; line-height: 1.55; }
.readme-body { font-size: 13px; line-height: 1.6; }
.readme-body :deep(h1), .readme-body :deep(h2), .readme-body :deep(h3) { margin: 12px 0 8px; font-size: 15px; }
.readme-body :deep(pre) {
  background: #f4f6f9; padding: 10px; border-radius: 8px; overflow: auto; font-size: 12px;
}
.vr-line { font-size: 12px; margin: 4px 0; }
.vr-line.err { color: var(--danger); }
.vr-line.warn { color: var(--warning); }
.file-tree {
  margin-top: 10px; background: #f7f8fa; border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px;
}
.file-tree-title { font-size: 12px; font-weight: 650; margin-bottom: 6px; }
.file-line {
  font-family: 'Cascadia Code', Consolas, monospace; font-size: 11px; color: #334; padding: 2px 0;
}
.file-line-btn {
  display: block; width: 100%; text-align: left; background: none; border: none;
  font-family: 'Cascadia Code', Consolas, monospace; font-size: 11px; color: #2451c7;
  padding: 3px 0; cursor: pointer;
}
.file-line-btn:hover { text-decoration: underline; }

.table-panel { padding: 0; overflow: hidden; }
.skill-table { width: 100%; border-collapse: collapse; }
.skill-table th {
  text-align: left; padding: 12px 16px; font-size: 12px; font-weight: 500;
  color: var(--muted); background: #fafbfc; border-bottom: 1px solid var(--border);
}
.skill-table td {
  padding: 14px 16px; border-bottom: 1px solid var(--border); vertical-align: middle; font-size: 13px;
}
.skill-table tr:hover td { background: #fafbfc; }
.ops { display: flex; flex-wrap: wrap; gap: 10px; }
.op-link {
  background: none; border: none; padding: 0; cursor: pointer;
  color: var(--primary); font-size: 13px; text-decoration: none;
}
.op-link:hover { text-decoration: underline; }
.op-link.danger { color: var(--danger); }
.empty-sm { text-align: center; color: var(--muted); padding: 40px 16px; font-size: 13px; }

.confirm-mask {
  position: fixed; inset: 0; background: var(--overlay); z-index: 110;
  display: flex; align-items: center; justify-content: center; padding: 16px;
}
.confirm-card {
  width: min(400px, 100%); background: #fff; border-radius: 14px;
  padding: 20px; box-shadow: var(--shadow-lg); border: 1px solid var(--border);
}
.confirm-title { display: flex; align-items: center; gap: 8px; font-size: 16px; font-weight: 650; }
.confirm-warn {
  width: 22px; height: 22px; border-radius: 50%;
  background: rgba(245, 165, 36, 0.15); color: #b7791f;
  display: inline-flex; align-items: center; justify-content: center; font-weight: 700;
}
.confirm-body { margin: 12px 0 0; font-size: 13px; line-height: 1.55; }
.confirm-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }

.type-bar { display: flex; align-items: center; gap: 12px; margin: 10px 0; font-size: 13px; }
.bar { flex: 1; height: 8px; background: var(--panel-2); border-radius: 4px; overflow: hidden; }
.bar-fill { height: 100%; background: linear-gradient(90deg, var(--primary), #7a5cff); border-radius: 4px; }
.top-item, .recent-item { display: flex; align-items: center; gap: 10px; padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
h3 { margin: 0 0 12px; }
@media (max-width: 1100px) {
  .audit-split { grid-template-columns: 1fr; }
  .audit-list, .audit-detail { max-height: none; }
  .stats-band { grid-template-columns: 1fr; }
}
</style>
