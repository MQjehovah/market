<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { marked } from 'marked'
import { api } from '../api'
import { authState } from '../stores/auth'
import { adminState } from '../stores/admin'
import {
  TYPE_LABELS,
  VISIBILITY_LABELS,
  reviewChecklistFor,
  reviewAutoFlags,
  reviewBlockReason,
  INSTALL_POLICY_LABELS,
  shelfLabel,
  formatDate,
  assetUrl,
  trialLabel
} from '../utils/format'
import StatusBadge from '../components/StatusBadge.vue'
import DebugCapabilityModal from '../components/DebugCapabilityModal.vue'
import PackagePreview from '../components/PackagePreview.vue'
import AdminTrialPanel from '../components/AdminTrialPanel.vue'
import RegistryImportPanel from '../components/RegistryImportPanel.vue'
import AdminTokensPanel from '../components/AdminTokensPanel.vue'
import ConfirmActionModal from '../components/ConfirmActionModal.vue'

const route = useRoute()
const router = useRouter()

const __API_BASE__ = (import.meta.env.BASE_URL || '/').replace(/\/$/, '') + '/api'

const SECTIONS = ['caps', 'users', 'gateway', 'tokens']
const SECTION_META = {
  caps: { label: '能力管理', hint: '审核上架、下架归档统一管理' },
  users: { label: '用户管理', hint: '按姓名、工号或部门查找，再改角色和启停' },
  gateway: { label: 'MCP 网关', hint: '把 stdio / HTTP / SSE 统一暴露为 HTTP 端点，供 Dify / Agent 接入' },
  tokens: {
    label: '服务令牌',
    hint: '给 Agent / CI / 网关消费方发 M2M Bearer（mkt_svc_…），免交互式登录'
  }
}

const section = computed(() => {
  const s = String(route.params.section || '')
  return SECTIONS.includes(s) ? s : 'caps'
})
const sectionTitle = computed(() => SECTION_META[section.value].label)
const sectionHint = computed(() => SECTION_META[section.value].hint)

/** 能力管理内部子页：审核队列 / 上架治理 */
const capsTab = ref('review')

watch(
  () => route.params.section,
  (s) => {
    const v = String(s)
    if (v === 'review' || v === 'listed') {
      const query = { ...route.query }
      if (v === 'listed') query.ctab = 'listed'
      else delete query.ctab
      router.replace({ path: '/admin/caps', query })
      return
    }
    if (!SECTIONS.includes(v)) router.replace('/admin/caps')
  },
  { immediate: true }
)

/** 审核队列：pending | rejected | returned */
const auditFilter = ref('pending')
const typeFilter = ref('')
const listedFilter = ref('published')
const auditQuery = ref('')
const listedQuery = ref('')
const loading = ref(true)
const showRegistry = ref(false)
const moreId = ref('')
const reviewAuto = ref([])
const reviewHistory = ref([])
const reviewHistoryLoading = ref(false)
const reviewHistoryError = ref('')
let historySeq = 0
let applyingQuery = false

const DECISION_LABELS = { reject: '拒绝', return: '打回', approve: '通过' }
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
const reviewingOwn = computed(
  () => Boolean(selectedCap.value && authState.user && selectedCap.value.author_id === authState.user.id)
)
const approveBlock = computed(() => reviewBlockReason(selectedCap.value))

const TYPE_COLORS = {
  plugin: '#2f6bff',
  agent: '#12b76a',
  workflow: '#7a5cff',
  skill: '#f5a524',
  mcp: '#0ea5e9',
  tool: '#64748b'
}

const userQuery = ref('')
const userRoleFilter = ref('')
const userStatusFilter = ref('')
const userDeptFilter = ref('')
const userMoreId = ref('')
const showCreateUser = ref(false)
const showEditUser = ref(false)
const editUser = ref(null)
const userNotice = ref('')
const emptyUserForm = () => ({
  username: '',
  email: '',
  password: '',
  name: '',
  work_id: '',
  phone: '',
  department: '',
  role: 'user'
})
const userForm = ref(emptyUserForm())
const editForm = ref({
  username: '',
  name: '',
  work_id: '',
  phone: '',
  email: '',
  department: '',
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

const queueTotal = computed(
  () => auditCounts.value.pending + auditCounts.value.rejected + auditCounts.value.returned
)

const auditList = computed(() => {
  let list =
    auditFilter.value === 'pending'
      ? reviewQueue.value
      : auditFilter.value === 'returned'
        ? returnedQueue.value
        : rejectedQueue.value
  if (typeFilter.value) list = list.filter((c) => c.type === typeFilter.value)
  const q = auditQuery.value.trim().toLowerCase()
  if (q) {
    list = list.filter((c) => capBlob(c).includes(q))
  }
  const rank = { high: 0, unknown: 1, medium: 2, none: 3 }
  return list.slice().sort((a, b) => {
    const diff = (rank[riskOf(a).key] ?? 9) - (rank[riskOf(b).key] ?? 9)
    if (diff) return diff
    const stamp = (c) => String(c.submitted_at || c.updated_at || '')
    return stamp(b).localeCompare(stamp(a))
  })
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
  let all = allCaps.value.filter((c) => ['published', 'deprecated'].includes(c.status))
  if (listedFilter.value !== 'all') all = all.filter((c) => c.status === listedFilter.value)
  if (typeFilter.value) all = all.filter((c) => c.type === typeFilter.value)
  const q = listedQuery.value.trim().toLowerCase()
  if (q) all = all.filter((c) => capBlob(c).includes(q))
  return all
})

const activeChecks = computed(() => reviewChecklistFor(selectedCap.value))

const pendingCheckLabels = computed(() =>
  activeChecks.value.filter((_, i) => !reviewChecks.value[i]).map((item) => item.short)
)

const lastDecision = computed(
  () => reviewHistory.value.find((row) => row.action === 'reject' || row.action === 'return') || null
)

const auditEmptyText = computed(() => {
  if (auditQuery.value.trim() || typeFilter.value) return '没有匹配的能力'
  if (auditFilter.value === 'pending') return '待审核队列已清空'
  if (auditFilter.value === 'rejected') return '没有已拒绝的能力'
  return '没有已打回的能力'
})

const listedEmptyText = computed(() => {
  if (listedQuery.value.trim() || typeFilter.value) return '没有匹配的能力'
  if (listedFilter.value === 'published') return '还没有已上架的能力'
  if (listedFilter.value === 'deprecated') return '没有已下架的能力'
  return '暂无记录'
})

function typeColor(type) {
  return TYPE_COLORS[type] || '#2f6bff'
}

function typeInitial(type) {
  return (TYPE_LABELS[type] || type || '?').slice(0, 1)
}

/** 默认头像用「名称/显示名」首字符（不是类型） */
function nameInitial(cap) {
  const n = String(cap?.display_name || cap?.name || '?').trim()
  return n ? n.slice(0, 1).toUpperCase() : '?'
}

const iconErrors = ref(new Set())
function markIconError(id) {
  iconErrors.value = new Set([...iconErrors.value, id])
}

function capTitle(cap) {
  return cap?.display_name || cap?.name || ''
}

function capBlob(cap) {
  return `${cap?.name || ''} ${cap?.display_name || ''} ${cap?.author_name || ''} ${cap?.description || ''}`.toLowerCase()
}

function validationOf(cap) {
  const vr = cap?.validation_report || (cap?.input_schema || {})._validation_report
  if (!vr || typeof vr !== 'object' || !Object.keys(vr).length) return null
  return vr
}

function applyAutoChecks(cap) {
  const items = reviewChecklistFor(cap)
  const flags = reviewAutoFlags(cap, items)
  reviewAuto.value = flags
  reviewChecks.value = flags.slice()
}

function riskOf(cap) {
  const vr = validationOf(cap)
  if (!vr) return { key: 'unknown', label: '未校验' }
  const errors = vr.errors || []
  const warnings = vr.warnings || []
  if (errors.length || vr.ok === false) return { key: 'high', label: '高风险' }
  if (warnings.length) return { key: 'medium', label: '中风险' }
  return { key: 'none', label: '无风险' }
}

function selectCap(cap) {
  if (!cap) return
  const changed = selectedId.value !== cap.id
  selectedId.value = cap.id
  if (!changed) return
  detailTab.value = 'intro'
  reviewComment.value = ''
  applyAutoChecks(cap)
  loadHistory(cap)
}

async function loadHistory(cap) {
  const seq = ++historySeq
  reviewHistory.value = []
  if (!cap || (cap.status !== 'rejected' && cap.status !== 'returned')) {
    reviewHistoryLoading.value = false
    return
  }
  reviewHistoryLoading.value = true
  reviewHistoryError.value = ''
  try {
    const rows = await api.get(`/admin/capabilities/${cap.id}/reviews`)
    if (seq !== historySeq) return
    reviewHistory.value = Array.isArray(rows) ? rows : []
  } catch {
    if (seq === historySeq) {
      reviewHistory.value = []
      reviewHistoryError.value = '暂时读不到审核记录'
    }
  } finally {
    if (seq === historySeq) reviewHistoryLoading.value = false
  }
}

function focusCaps(target) {
  showTrial.value = false
  if (target === 'usage') {
    capsTab.value = 'review'
    showStatsDetail.value = true
    return
  }
  showStatsDetail.value = false
  if (target === 'pending') {
    capsTab.value = 'review'
    auditFilter.value = 'pending'
    return
  }
  capsTab.value = 'listed'
  listedFilter.value = 'published'
}

watch(
  () => [route.query.ctab, route.query.aq, route.query.type, route.query.listed],
  async () => {
    applyingQuery = true
    const q = route.query
    const nextTab = q.ctab === 'listed' ? 'listed' : 'review'
    const nextAudit = q.aq === 'rejected' || q.aq === 'returned' ? String(q.aq) : 'pending'
    const nextType = typeof q.type === 'string' ? q.type : ''
    const nextListed = q.listed === 'deprecated' || q.listed === 'all' ? String(q.listed) : 'published'
    if (capsTab.value !== nextTab) capsTab.value = nextTab
    if (auditFilter.value !== nextAudit) auditFilter.value = nextAudit
    if (typeFilter.value !== nextType) typeFilter.value = nextType
    if (listedFilter.value !== nextListed) listedFilter.value = nextListed
    await nextTick()
    applyingQuery = false
  },
  { immediate: true }
)

watch([capsTab, auditFilter, typeFilter, listedFilter], () => {
  if (applyingQuery || section.value !== 'caps') return
  const query = { ...route.query }
  if (capsTab.value === 'listed') query.ctab = 'listed'
  else delete query.ctab
  if (auditFilter.value !== 'pending') query.aq = auditFilter.value
  else delete query.aq
  if (typeFilter.value) query.type = typeFilter.value
  else delete query.type
  if (listedFilter.value !== 'published') query.listed = listedFilter.value
  else delete query.listed
  const keys = ['ctab', 'aq', 'type', 'listed']
  if (keys.every((key) => String(query[key] || '') === String(route.query[key] || ''))) return
  router.replace({ query })
})

watch(auditList, (list) => {
  if (list.find((c) => c.id === selectedId.value)) return
  if (list[0]) selectCap(list[0])
  else {
    selectedId.value = ''
    reviewHistory.value = []
  }
})

const roleDefs = [
  { key: 'admin', label: '管理员', desc: '审核上架、下架归档、用户管理、试用全部资产、MCP 网关、服务令牌' },
  { key: 'user', label: '普通用户', desc: '登录即可发布与在线编辑能力、提交审核；试用自己创建或已加入的资产；生产消费走 cap install / MCP' }
]

const userFilterOn = computed(() =>
  Boolean(userQuery.value.trim() || userRoleFilter.value || userStatusFilter.value || userDeptFilter.value)
)
const filteredUsers = computed(() => {
  const q = userQuery.value.trim().toLowerCase()
  return users.value.filter((u) => {
    if (userRoleFilter.value && u.role !== userRoleFilter.value) return false
    if (userStatusFilter.value === 'active' && !u.is_active) return false
    if (userStatusFilter.value === 'disabled' && u.is_active) return false
    if (userDeptFilter.value && (u.department || '') !== userDeptFilter.value) return false
    if (!q) return true
    return [u.username, u.name, u.email, u.work_id, u.phone, u.department].some((v) =>
      String(v || '').toLowerCase().includes(q)
    )
  })
})
function roleLabel(role) {
  return roleDefs.find((r) => r.key === role)?.label || role
}
function userTitle(u) {
  return (u.name || '').trim() || u.username || '—'
}
function userSub(u) {
  const work = (u.work_id || '').trim()
  const login = (u.username || '').trim()
  const title = userTitle(u)
  const bits = []
  if (work && work !== title) bits.push(work)
  if (login && login !== title && login !== work) bits.push(login)
  return bits.join(' · ')
}

const departmentOptions = computed(() => [
  ...new Set(users.value.map((u) => (u.department || '').trim()).filter(Boolean))
])

async function load() {
  error.value = ''
  loading.value = true
  try {
    const [queue, rejected, returned, caps, userList, stat, gatewayList] = await Promise.all([
      api.get('/admin/capabilities?status_filter=reviewing'),
      api.get('/admin/capabilities?status_filter=rejected'),
      api.get('/admin/capabilities?status_filter=returned'),
      api.get('/admin/capabilities'),
      api.get('/admin/users'),
      api.get('/admin/stats'),
      api.get('/admin/mcp-gateway/servers')
    ])
    reviewQueue.value = queue
    rejectedQueue.value = rejected
    returnedQueue.value = returned
    allCaps.value = caps
    users.value = userList
    stats.value = stat
    gatewayServers.value = gatewayList
    adminState.reviewingCount = reviewQueue.value.length
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function review(cap, action) {
  if (!cap) return
  try {
    await api.post(`/admin/capabilities/${cap.id}/review`, { action, comment: reviewComment.value })
    notice.value =
      action === 'approve'
        ? `「${capTitle(cap)}」已通过并上架`
        : action === 'reject'
          ? `「${capTitle(cap)}」已拒绝`
          : `「${capTitle(cap)}」已打回修改`
    reviewComment.value = ''
    confirmReview.value = null
    await load()
  } catch (e) {
    error.value = e.message
  }
}

function askApprove(cap) {
  const live = cap.live_version
  const title = capTitle(cap)
  confirmReview.value = {
    action: 'approve',
    title: '确定通过并上架？',
    body: live
      ? `「${title}」通过后立即上架。已上架的 v${live} 会变为已弃用。`
      : `「${title}」通过后立即上架。`,
    okText: '通过并上架',
    danger: false,
    cap
  }
}

function askReject(cap) {
  confirmReview.value = {
    action: 'reject',
    title: '确定拒绝？',
    body: `拒绝后，「${capTitle(cap)}」状态为「已驳回」，留在已拒绝列表。作者按意见修改后可以重新提交。`,
    okText: '拒绝',
    danger: true,
    cap
  }
}

function askReturn(cap) {
  confirmReview.value = {
    action: 'return',
    title: '确定打回？',
    body: `打回后，「${capTitle(cap)}」状态为「已打回」，不会变回草稿。作者按意见修改后可以重新提交。`,
    okText: '打回',
    danger: false,
    cap
  }
}

async function confirmReviewOk() {
  const action = confirmReview.value
  if (!action) return
  if (['reject', 'return'].includes(action.action) && !reviewComment.value.trim()) {
    error.value = '请填写审核意见，作者会在详情页看到'
    return
  }
  await review(action.cap, action.action)
}

async function statusAction(cap, action) {
  try {
    await api.post(`/admin/capabilities/${cap.id}/${action}`)
    notice.value = action === 'deprecate' ? `「${capTitle(cap)}」已下架` : `「${capTitle(cap)}」已归档`
    await load()
  } catch (e) {
    error.value = e.message
  }
}

/** 可信认证: 标记/取消管理员认证(前端展示徽标) */
async function toggleVerify(cap) {
  try {
    await api.post(`/admin/capabilities/${cap.id}/verify`, { verified: !cap.verified })
    notice.value = cap.verified ? `已取消「${cap.name}」的认证` : `已认证「${cap.name}」`
    await load()
  } catch (e) {
    error.value = e.message
  }
}

/** 安装策略: optional / default_on / required(组织级必装) */
async function setInstallPolicy(cap, policy) {
  try {
    await api.post(`/capabilities/${cap.id}/install-policy`, { install_policy: policy })
    cap.install_policy = policy
    notice.value = `「${cap.name}」安装策略已设为 ${INSTALL_POLICY_LABELS[policy] || policy}`
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
    error.value = '请填写登录名、邮箱和密码'
    return
  }
  try {
    const u = await api.post('/admin/users', {
      username: f.username,
      email: f.email,
      password: f.password,
      name: f.name,
      work_id: f.work_id,
      phone: f.phone,
      department: f.department,
      role: f.role
    })
    userNotice.value = `已创建用户 ${u.username}（${roleDefs.find((r) => r.key === u.role)?.label}）`
    showCreateUser.value = false
    userForm.value = emptyUserForm()
    await load()
  } catch (e) {
    error.value = e.message
  }
}

function isSelf(u) {
  return authState.user?.id === u.id
}

function openEditUser(u) {
  userMoreId.value = ''
  editUser.value = u
  editForm.value = {
    username: u.username,
    name: u.name || '',
    work_id: u.work_id || '',
    phone: u.phone || '',
    email: u.email || '',
    department: u.department || '',
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
  if (f.name !== (editUser.value.name || '')) patch.name = f.name
  if (f.work_id !== (editUser.value.work_id || '')) patch.work_id = f.work_id
  if (f.phone !== (editUser.value.phone || '')) patch.phone = f.phone
  if (f.email !== (editUser.value.email || '')) patch.email = f.email
  if (f.department !== (editUser.value.department || '')) patch.department = f.department
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

function askSetRole(u, role) {
  userMoreId.value = ''
  const label = roleLabel(role)
  confirmAction.value = {
    kind: 'user-role',
    title: `将「${userTitle(u)}」设为${label}？`,
    body: role === 'admin'
      ? '管理员可以审核上架、管理用户、网关和服务令牌。'
      : '改为普通用户后，不能再审核上架或管理其他账号。',
    okText: `设为${label}`,
    danger: role === 'admin',
    payload: { user: u, role }
  }
}

function askToggleActive(u) {
  userMoreId.value = ''
  const title = userTitle(u)
  confirmAction.value = {
    kind: 'user-active',
    title: u.is_active ? `禁用「${title}」？` : `启用「${title}」？`,
    body: u.is_active ? '禁用后该账号不能登录。' : '启用后该账号可以重新登录。',
    okText: u.is_active ? '禁用' : '启用',
    danger: !!u.is_active,
    payload: u
  }
}

async function removeUser(u) {
  error.value = ''
  userNotice.value = ''
  userMoreId.value = ''
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

function askStatus(cap, action) {
  moreId.value = ''
  const title = capTitle(cap)
  confirmAction.value = {
    kind: 'cap-status',
    title: action === 'deprecate' ? `下架「${title}」？` : `归档「${title}」？`,
    body:
      action === 'deprecate'
        ? '下架后，该能力不再作为可安装的上架能力，仍可在「已下架」中查看。'
        : '归档后，该能力会离开上架治理列表（已上架 / 已下架），本页不能撤销。',
    okText: action === 'deprecate' ? '下架' : '归档',
    danger: true,
    payload: { cap, action }
  }
}

async function confirmActionOk() {
  const action = confirmAction.value
  confirmAction.value = null
  if (!action) return
  if (action.kind === 'remove-user') await doRemoveUser(action.payload)
  if (action.kind === 'user-role') await updateUser(action.payload.user, { role: action.payload.role })
  if (action.kind === 'user-active') await updateUser(action.payload, { is_active: !action.payload.is_active })
  if (action.kind === 'remove-gateway') await doRemoveGateway(action.payload)
  if (action.kind === 'cap-status') await statusAction(action.payload.cap, action.payload.action)
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

function copyText(text) {
  navigator.clipboard?.writeText(text).then(() => {
    gatewayNotice.value = '已复制到剪贴板'
  })
}

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

function closeMore() {
  moreId.value = ''
  userMoreId.value = ''
}

onMounted(() => {
  document.addEventListener('click', closeMore)
  if (route.query.trial === '1') showTrial.value = true
  load()
})

onUnmounted(() => {
  document.removeEventListener('click', closeMore)
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
          <h2 class="page-title">{{ sectionTitle }}</h2>
          <div v-if="stats && section === 'caps'" class="stat-line">
            <span><b>{{ stats.total_capabilities }}</b> 个能力</span>
            <button type="button" @click="focusCaps('published')"><b class="ok">{{ stats.published_count }}</b> 已上架</button>
            <button type="button" @click="focusCaps('pending')"><b class="warn">{{ stats.reviewing_count }}</b> 待审核</button>
            <button type="button" @click="focusCaps('usage')"><b>{{ stats.total_usage }}</b> 次用量</button>
          </div>
          <div v-else class="muted section-hint">{{ sectionHint }}</div>
        </div>
        <div class="header-right">
          <button
            v-if="section === 'caps' && capsTab === 'review' && stats"
            class="btn"
            type="button"
            @click="showStatsDetail = !showStatsDetail"
          >{{ showStatsDetail ? '收起统计' : '统计明细' }}</button>
          <AdminTrialPanel
            v-if="section === 'caps'"
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

      <!-- 能力管理：审核队列 / 上架治理 -->
      <section v-if="section === 'caps'" class="desk">
        <div class="desk-head">
          <div class="seg">
            <button
              type="button"
              class="seg-item"
              :class="{ active: capsTab === 'review' }"
              @click="capsTab = 'review'"
            >
              审核队列 <span>{{ queueTotal }}</span>
            </button>
            <button
              type="button"
              class="seg-item"
              :class="{ active: capsTab === 'listed' }"
              @click="capsTab = 'listed'"
            >
              上架治理 <span>{{ listedCounts.all }}</span>
            </button>
          </div>
          <button class="btn btn-sm" type="button" @click="showRegistry = !showRegistry">
            {{ showRegistry ? '收起 Registry 导入' : '从 Registry 导入' }}
          </button>
        </div>
        <RegistryImportPanel v-if="showRegistry" />

        <template v-if="capsTab === 'review'">
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
              <router-link :to="`/capabilities/${item.id}`">{{ item.display_name || item.name }}</router-link>
              <span class="badge">{{ TYPE_LABELS[item.type] || item.type }}</span>
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
          <div class="text-tabs">
            <button type="button" class="text-tab" :class="{ active: auditFilter === 'pending' }" @click="auditFilter = 'pending'">
              待审核 <em>{{ auditCounts.pending }}</em>
            </button>
            <button type="button" class="text-tab" :class="{ active: auditFilter === 'rejected' }" @click="auditFilter = 'rejected'">
              已拒绝 <em>{{ auditCounts.rejected }}</em>
            </button>
            <button type="button" class="text-tab" :class="{ active: auditFilter === 'returned' }" @click="auditFilter = 'returned'">
              已打回 <em>{{ auditCounts.returned }}</em>
            </button>
          </div>
          <div class="toolbar-filters">
            <input v-model="auditQuery" class="input queue-search" type="search" placeholder="搜索名称、作者" />
            <select v-model="typeFilter" class="select type-select">
              <option value="">全部类型</option>
              <option v-for="t in Object.keys(TYPE_LABELS)" :key="t" :value="t">{{ TYPE_LABELS[t] }}</option>
            </select>
          </div>
        </div>

        <div class="audit-split" :class="{ 'with-stats': showStatsDetail }">
            <div class="audit-list panel">
              <div class="list-head">
                <span>能力 · {{ auditList.length }} 条</span>
                <span title="高风险与未校验排在前面">风险</span>
              </div>
              <div v-if="loading && auditList.length === 0" class="empty-sm">正在加载审核队列…</div>
              <div v-else-if="!loading && auditList.length === 0" class="empty-sm">{{ auditEmptyText }}</div>
              <button
                v-for="cap in auditList"
                :key="cap.id"
                type="button"
                class="list-row"
                :class="{ active: selectedId === cap.id }"
                @click="selectCap(cap)"
              >
                <div class="skill-cell">
                  <img
                    v-if="cap.icon_url && !iconErrors.has(cap.id)"
                    class="skill-icon icon-img"
                    :src="assetUrl(cap.icon_url)"
                    :alt="cap.name"
                    @error="markIconError(cap.id)"
                  />
                  <div v-else class="skill-icon" :style="{ background: typeColor(cap.type) }">{{ nameInitial(cap) }}</div>
                  <div class="skill-meta">
                    <div class="skill-name-row">
                      <span class="skill-name">{{ capTitle(cap) }}</span>
                      <span class="ver-tag">v{{ cap.version }}</span>
                    </div>
                    <div class="skill-sub muted">
                      <span>{{ TYPE_LABELS[cap.type] || cap.type }}</span>
                      <span>{{ cap.author_name || '—' }}</span>
                      <span>{{ formatDate(cap.submitted_at || cap.updated_at) }}</span>
                    </div>
                  </div>
                </div>
                <span class="risk" :class="riskOf(cap).key">{{ riskOf(cap).label }}</span>
              </button>
            </div>

            <div class="audit-detail panel">
              <div v-if="!selectedCap" class="empty-sm">
                {{ loading ? '正在加载审核队列…' : (auditList.length ? '从左侧选择一条能力' : '队列里没有需要处理的能力') }}
              </div>
              <template v-else>
                <div class="detail-scroll">
                <div class="detail-top">
                  <div class="detail-identity">
                    <img
                      v-if="selectedCap.icon_url && !iconErrors.has(selectedCap.id)"
                      class="skill-icon lg icon-img"
                      :src="assetUrl(selectedCap.icon_url)"
                      :alt="capTitle(selectedCap)"
                      @error="markIconError(selectedCap.id)"
                    />
                    <div v-else class="skill-icon lg" :style="{ background: typeColor(selectedCap.type) }">{{ nameInitial(selectedCap) }}</div>
                    <div>
                      <div class="detail-name-row">
                        <h3 class="detail-name">{{ capTitle(selectedCap) }}</h3>
                        <StatusBadge :status="selectedCap.status" />
                      </div>
                      <div class="muted" style="font-size: 12px; margin-top: 4px">
                        {{ TYPE_LABELS[selectedCap.type] }}
                        <span v-if="shelfLabel(selectedCap.type)"> · {{ shelfLabel(selectedCap.type) }}</span>
                      </div>
                    </div>
                  </div>
                </div>

                <div class="meta-grid">
                  <div class="meta-cell"><span class="meta-k">开发者</span><span class="meta-v">{{ selectedCap.author_name || '—' }}</span></div>
                  <div class="meta-cell"><span class="meta-k">部门</span><span class="meta-v">{{ selectedCap.organization || '个人' }}</span></div>
                  <div class="meta-cell"><span class="meta-k">加入次数</span><span class="meta-v">{{ selectedCap.usage_count || 0 }}</span></div>
                  <div class="meta-cell"><span class="meta-k">提交时间</span><span class="meta-v">{{ formatDate(selectedCap.submitted_at || selectedCap.updated_at) }}</span></div>
                  <div v-if="selectedCap.live_version" class="meta-cell"><span class="meta-k">当前上架</span><span class="meta-v">v{{ selectedCap.live_version }}</span></div>
                  <div class="meta-cell"><span class="meta-k">版本</span><span class="meta-v">v{{ selectedCap.version }}</span></div>
                  <div class="meta-cell">
                    <span class="meta-k">可见范围</span>
                    <span class="meta-v"><span class="vis-pill">{{ VISIBILITY_LABELS[selectedCap.visibility] || selectedCap.visibility }}</span></span>
                  </div>
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
                      <div class="file-tree-title">包内文件（打开「文件预览」查看内容）</div>
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
                  <div v-else class="muted" style="font-size: 13px">暂无校验报告。风险列会显示「未校验」，通过前请打开文件预览核对。</div>
                </div>
                </div>

                <div v-if="selectedCap.status === 'reviewing'" class="decision-dock">
                  <div class="check-title">审核清单</div>
                  <div class="check-list">
                    <label v-for="(item, i) in activeChecks" :key="item.index" class="check-item">
                      <input v-model="reviewChecks[i]" type="checkbox" />
                      <span class="check-label">{{ item.text }}</span>
                      <em v-if="reviewAuto[i]" class="auto-tag">{{ item.index === 1 ? '未见硬编码' : '结构通过' }}</em>
                    </label>
                  </div>
                  <textarea
                    v-model="reviewComment"
                    class="textarea"
                    rows="2"
                    placeholder="审核意见（拒绝 / 打回必填，作者会在详情页看到）"
                  ></textarea>
                  <div v-if="reviewingOwn" class="muted decision-hint">这是你提交的。请在「我的能力」撤回，或等其他管理员审核。</div>
                  <div v-else class="decision-row">
                    <div class="muted decision-hint">
                      <template v-if="approveBlock">{{ approveBlock }}</template>
                      <template v-else-if="allReviewChecked">清单已齐，可以通过{{ selectedCap.live_version ? `。已上架的 v${selectedCap.live_version} 会变为已弃用` : '' }}</template>
                      <template v-else>还差 {{ pendingCheckLabels.length }} 项：{{ pendingCheckLabels.join('、') }}</template>
                    </div>
                    <div class="muted decision-hint">打回后是「已打回」，拒绝后是「已驳回」。都要写意见，作者改完可以再交。</div>
                    <div class="detail-actions">
                      <button class="btn btn-danger" type="button" @click="askReject(selectedCap)">拒绝</button>
                      <button class="btn" type="button" @click="askReturn(selectedCap)">打回</button>
                      <button
                        class="btn btn-primary"
                        type="button"
                        :disabled="!allReviewChecked || !!approveBlock"
                        :title="approveBlock || (allReviewChecked ? '通过并上架' : '还差 ' + pendingCheckLabels.join('、'))"
                        @click="askApprove(selectedCap)"
                      >通过</button>
                    </div>
                  </div>
                </div>
                <div v-else class="decision-dock">
                  <div class="check-title">{{ selectedCap.status === 'rejected' ? '已拒绝' : '已打回' }} · 等待作者修改后重新提交</div>
                  <div v-if="reviewHistoryLoading" class="muted decision-hint">正在读取审核记录…</div>
                  <div v-else-if="reviewHistoryError" class="muted decision-hint">
                    {{ reviewHistoryError }}
                    <button class="btn btn-sm" type="button" style="margin-left: 8px" @click="loadHistory(selectedCap)">重试</button>
                  </div>
                  <div v-else-if="lastDecision" class="history-comment">
                    <span class="meta-k">上次{{ DECISION_LABELS[lastDecision.action] || '结论' }}</span>
                    {{ lastDecision.comment || '（无审核意见）' }}
                    <span class="muted"> · {{ formatDate(lastDecision.created_at) }}</span>
                  </div>
                  <div v-else class="muted decision-hint">暂无审核意见</div>
                </div>
               </template>
             </div>
           </div>
        </template>

        <template v-else>
         <div class="desk-toolbar">
          <div class="text-tabs">
            <button type="button" class="text-tab" :class="{ active: listedFilter === 'published' }" @click="listedFilter = 'published'">
              已上架 <em>{{ listedCounts.published }}</em>
            </button>
            <button type="button" class="text-tab" :class="{ active: listedFilter === 'deprecated' }" @click="listedFilter = 'deprecated'">
              已下架 <em>{{ listedCounts.deprecated }}</em>
            </button>
            <button type="button" class="text-tab" :class="{ active: listedFilter === 'all' }" @click="listedFilter = 'all'">
              全部 <em>{{ listedCounts.all }}</em>
            </button>
          </div>
          <div class="toolbar-filters">
            <input v-model="listedQuery" class="input queue-search" type="search" placeholder="搜索名称、作者" />
            <select v-model="typeFilter" class="select type-select">
              <option value="">全部类型</option>
              <option v-for="t in Object.keys(TYPE_LABELS)" :key="t" :value="t">{{ TYPE_LABELS[t] }}</option>
            </select>
          </div>
        </div>
        <div class="panel table-panel">
          <div v-if="loading && listedCaps.length === 0" class="empty">正在加载上架列表…</div>
          <div v-else-if="listedCaps.length === 0" class="empty">{{ listedEmptyText }}</div>
          <table v-else class="skill-table">
            <thead>
              <tr>
                <th style="width: 36%">能力</th>
                <th>状态</th>
                <th>可见范围</th>
                <th>作者</th>
                <th>使用量</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="cap in listedCaps" :key="cap.id">
                <td>
                  <div class="skill-cell">
                    <img
                      v-if="cap.icon_url && !iconErrors.has(cap.id)"
                      class="skill-icon icon-img"
                      :src="assetUrl(cap.icon_url)"
                      :alt="cap.name"
                      @error="markIconError(cap.id)"
                    />
                    <div v-else class="skill-icon" :style="{ background: typeColor(cap.type) }">{{ nameInitial(cap) }}</div>
                    <div class="skill-meta">
                      <div class="skill-name-row">
                        <router-link class="skill-name link" :to="`/capabilities/${cap.id}`">{{ cap.display_name || cap.name }}</router-link>
                        <span v-if="cap.verified" class="ver-tag" style="color: var(--success); border-color: var(--success)">认证</span>
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
                    <button class="op-link" type="button" @click="toggleVerify(cap)">{{ cap.verified ? '取消认证' : '认证' }}</button>
                    <select
                      class="select policy-select"
                      :value="cap.install_policy || 'optional'"
                      title="安装策略（必装 = 组织级强制）"
                      @change="setInstallPolicy(cap, $event.target.value)"
                    >
                      <option value="optional">可选安装</option>
                      <option value="default_on">默认安装</option>
                      <option value="required">组织必装</option>
                    </select>
                    <div class="more-wrap" @click.stop>
                      <button class="op-link" type="button" @click="moreId = moreId === cap.id ? '' : cap.id">更多</button>
                      <div v-if="moreId === cap.id" class="more-menu">
                        <button v-if="cap.status === 'published'" type="button" class="danger" @click="askStatus(cap, 'deprecate')">下架</button>
                        <button
                          v-if="cap.status === 'published' || cap.status === 'deprecated'"
                          type="button"
                          class="danger"
                          @click="askStatus(cap, 'archive')"
                        >归档</button>
                      </div>
                    </div>
                  </div>
                </td>
              </tr>
             </tbody>
           </table>
         </div>
        </template>
      </section>

      <section v-else-if="section === 'users'" class="panel">
        <div class="desk-toolbar">
          <div class="toolbar-filters">
            <input v-model="userQuery" class="input queue-search" placeholder="姓名、工号、手机、部门" />
            <select v-model="userRoleFilter" class="select type-select">
              <option value="">全部角色</option>
              <option value="admin">管理员</option>
              <option value="user">普通用户</option>
            </select>
            <select v-model="userStatusFilter" class="select type-select">
              <option value="">全部状态</option>
              <option value="active">正常</option>
              <option value="disabled">禁用</option>
            </select>
            <select v-model="userDeptFilter" class="select user-dept-select">
              <option value="">全部部门</option>
              <option v-for="d in departmentOptions" :key="d" :value="d">{{ d }}</option>
            </select>
            <span class="muted user-count">
              <template v-if="userFilterOn">{{ filteredUsers.length }} / {{ users.length }} 人</template>
              <template v-else>共 {{ users.length }} 人</template>
            </span>
          </div>
          <button class="btn btn-primary" type="button" @click="showCreateUser = true">新增用户</button>
        </div>
        <div v-if="userNotice" class="alert alert-success">{{ userNotice }}</div>
        <table class="table">
          <thead>
            <tr><th>用户</th><th>联系方式</th><th>部门</th><th>角色</th><th>状态</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-if="filteredUsers.length === 0">
              <td colspan="6" class="muted">{{ userFilterOn ? '无匹配用户' : '暂无用户' }}</td>
            </tr>
            <tr v-for="u in filteredUsers" :key="u.id">
              <td>
                <div class="user-name">{{ userTitle(u) }}</div>
                <div v-if="userSub(u)" class="muted user-sub">{{ userSub(u) }}</div>
              </td>
              <td>
                <div class="user-contact">{{ u.phone || '—' }}</div>
                <div v-if="u.email" class="muted user-sub">{{ u.email }}</div>
              </td>
              <td>{{ u.department || '—' }}</td>
              <td><span class="badge" :class="u.role === 'admin' ? 'badge-primary' : ''">{{ roleLabel(u.role) }}</span></td>
              <td>
                <div class="user-status">
                  <span class="badge" :class="u.is_active ? 'badge-success' : 'badge-danger'">{{ u.is_active ? '正常' : '禁用' }}</span>
                  <span v-if="isSelf(u)" class="badge">当前账号</span>
                </div>
              </td>
              <td>
                <div class="ops">
                  <button class="op-link" type="button" @click="openEditUser(u)">编辑</button>
                  <div v-if="!isSelf(u)" class="more-wrap" @click.stop>
                    <button class="op-link" type="button" @click="userMoreId = userMoreId === u.id ? '' : u.id">更多</button>
                    <div v-if="userMoreId === u.id" class="more-menu">
                      <button v-if="u.role !== 'admin'" type="button" @click="askSetRole(u, 'admin')">设为管理员</button>
                      <button v-else type="button" @click="askSetRole(u, 'user')">设为普通用户</button>
                      <button type="button" :class="{ danger: u.is_active }" @click="askToggleActive(u)">{{ u.is_active ? '禁用' : '启用' }}</button>
                      <button type="button" class="danger" @click="removeUser(u)">删除</button>
                    </div>
                  </div>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
        <datalist id="dept-suggest">
          <option v-for="d in departmentOptions" :key="d" :value="d"></option>
        </datalist>
      </section>

      <section v-else-if="section === 'gateway'">
        <div class="flex-between mb-16" style="align-items: flex-end">
          <p class="muted" style="font-size: 13px; max-width: 720px; margin: 0">
            外部 MCP 客户端（Dify、Claude Desktop、其他 Agent）用令牌接入；
            市场连接器能力包也可以用 transport=gateway 引用这里的服务。
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

      <AdminTokensPanel v-else-if="section === 'tokens'" />

    <ConfirmActionModal
      :show="!!confirmAction"
      :title="confirmAction?.title || ''"
      :body="confirmAction?.body || ''"
      :ok-text="confirmAction?.okText || '确定'"
      :danger="!!confirmAction?.danger"
      @ok="confirmActionOk"
      @cancel="confirmAction = null"
    />
    <DebugCapabilityModal :show="!!debugCap" :cap="debugCap" :title="trialLabel(debugCap?.type)" @close="debugCap = null" />

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
          :placeholder="['reject', 'return'].includes(confirmReview.action) ? '审核意见（必填，作者会在详情页看到）' : '审核意见（可选）'"
          style="margin-top: 12px"
        ></textarea>
        <div class="confirm-actions">
          <button class="btn" type="button" @click="confirmReview = null">取消</button>
          <button
            class="btn"
            :class="confirmReview.danger ? 'btn-danger' : 'btn-primary'"
            type="button"
            :disabled="['reject', 'return'].includes(confirmReview.action) && !reviewComment.trim()"
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
          <div class="field"><label>姓名</label><input v-model="userForm.name" class="input" /></div>
          <div class="field"><label>工号</label><input v-model="userForm.work_id" class="input" placeholder="与登录名不同时填写" /></div>
          <div class="field"><label>登录名 *</label><input v-model="userForm.username" class="input" placeholder="3-64 位字母数字/._-" /></div>
          <div class="field"><label>邮箱 *</label><input v-model="userForm.email" class="input" placeholder="user@example.com" /></div>
          <div class="field"><label>手机</label><input v-model="userForm.phone" class="input" /></div>
          <div class="field"><label>部门</label><input v-model="userForm.department" class="input" list="dept-suggest" placeholder="如：研发部" /></div>
          <div class="field"><label>初始密码 *</label><input v-model="userForm.password" type="password" class="input" placeholder="至少 6 位" /></div>
        </div>
        <div class="field mt-12">
          <label>角色</label>
          <select v-model="userForm.role" class="select">
            <option v-for="r in roleDefs" :key="r.key" :value="r.key">{{ r.label }}</option>
          </select>
          <span class="muted role-hint">{{ roleDefs.find((r) => r.key === userForm.role)?.desc }}</span>
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
          <div class="field"><label>姓名</label><input v-model="editForm.name" class="input" /></div>
          <div class="field"><label>工号</label><input v-model="editForm.work_id" class="input" /></div>
          <div class="field"><label>登录名</label><input v-model="editForm.username" class="input" /></div>
          <div class="field"><label>邮箱</label><input v-model="editForm.email" class="input" /></div>
          <div class="field"><label>手机</label><input v-model="editForm.phone" class="input" /></div>
          <div class="field"><label>部门</label><input v-model="editForm.department" class="input" list="dept-suggest" placeholder="如：研发部" /></div>
          <div class="field"><label>重置密码（留空不改）</label><input v-model="editForm.password" type="password" class="input" placeholder="至少 6 位" /></div>
        </div>
        <div v-if="error" class="alert alert-error mt-12">{{ error }}</div>
        <div class="modal-foot">
          <span class="muted" style="font-size: 12px">角色和启停在列表的「更多」里修改</span>
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
.desk-head {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  flex-wrap: wrap; margin-bottom: 14px;
}
.desk-head .seg { margin-bottom: 0; }
.desk-toolbar {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  flex-wrap: wrap; margin-bottom: 14px;
}
.desk-toolbar .seg { margin-bottom: 0; }
.toolbar-filters { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.queue-search { width: 200px; max-width: 100%; }
.type-select { width: 140px; }
.user-dept-select { width: 160px; }
.user-count { font-size: 13px; white-space: nowrap; }
.user-name { font-weight: 650; }
.user-sub { font-size: 12px; margin-top: 2px; }
.user-contact { font-size: 13px; }
.user-status { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.role-hint { display: block; margin-top: 6px; font-size: 12px; line-height: 1.5; }
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
.page-title { margin: 0; font-size: 20px; }
.section-hint { font-size: 13px; margin-top: 2px; }
.stat-line {
  display: flex; flex-wrap: wrap; gap: 2px 14px; margin-top: 4px;
  font-size: 13px; color: var(--muted);
}
.stat-line b { font-weight: 650; font-variant-numeric: tabular-nums; color: var(--text); }
.stat-line b.ok { color: var(--success); }
.stat-line b.warn { color: #b7791f; }
.stat-line button {
  background: none; border: none; padding: 0; cursor: pointer;
  font: inherit; color: var(--muted);
}
.stat-line button:hover { color: var(--primary); }
.header-chips { display: flex; gap: 10px; flex-wrap: wrap; }
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
.chip-btn { cursor: pointer; font: inherit; color: var(--muted); }
.chip-btn:hover { border-color: var(--primary); }
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
.text-tabs { display: flex; align-items: center; gap: 2px; }
.text-tab {
  background: none; border: none; color: var(--muted); padding: 6px 10px;
  border-radius: 8px; cursor: pointer; font-size: 13px;
}
.text-tab em { font-style: normal; margin-left: 4px; font-size: 12px; font-variant-numeric: tabular-nums; }
.text-tab:hover { color: var(--text); }
.text-tab.active { color: var(--text); font-weight: 650; background: var(--panel-2); }
.text-tab.active em { color: var(--primary); }

.audit-split {
  display: grid; grid-template-columns: minmax(280px, 380px) 1fr; gap: 14px; align-items: stretch;
  height: calc(100vh - 188px); min-height: 420px;
}
.audit-split.with-stats { height: calc(100vh - 430px); min-height: 320px; }
.audit-list { padding: 0; overflow: auto; min-height: 0; height: auto; max-height: none; }
.list-head {
  display: flex; justify-content: space-between; padding: 10px 14px;
  font-size: 12px; color: var(--muted); background: #fafbfc; border-bottom: 1px solid var(--border);
  position: sticky; top: 0;
}
.list-row {
  width: 100%; display: flex; align-items: flex-start; justify-content: space-between; gap: 10px;
  text-align: left; background: none; border: none; border-bottom: 1px solid var(--border);
  padding: 12px 14px; cursor: pointer; box-shadow: inset 3px 0 0 transparent;
}
.list-row:hover { background: #fafbfc; }
.list-row.active { background: var(--primary-soft); box-shadow: inset 3px 0 0 var(--primary); }
.risk {
  font-size: 11px; line-height: 1.4; white-space: nowrap; flex: none;
  margin-top: 2px; padding: 1px 8px; border-radius: 999px;
}
.risk.none { color: var(--muted); background: transparent; padding-right: 0; }
.risk.unknown { color: #475569; background: var(--panel-2); border: 1px solid var(--border); }
.risk.medium { color: #b7791f; background: rgba(245, 165, 36, 0.14); }
.risk.high { color: var(--danger); background: rgba(229, 72, 77, 0.1); font-weight: 650; }

.skill-cell { display: flex; gap: 10px; align-items: flex-start; min-width: 0; }
.skill-icon {
  width: 36px; height: 36px; border-radius: 9px; flex: none;
  color: #fff; font-weight: 700; font-size: 14px;
  display: flex; align-items: center; justify-content: center;
}
.skill-icon.lg { width: 48px; height: 48px; border-radius: 12px; font-size: 18px; }
.icon-img { object-fit: cover; background: var(--panel-2); }
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
.skill-sub {
  display: flex; flex-wrap: wrap; gap: 0 2px; margin-top: 3px; font-size: 12px;
}
.skill-sub span:not(:last-child)::after { content: '·'; margin: 0 6px; color: #c5cad3; }

.audit-detail {
  padding: 0; min-height: 0; max-height: none; height: auto;
  display: flex; flex-direction: column; overflow: hidden;
}
.detail-scroll { flex: 1; min-height: 0; overflow: auto; padding: 18px 20px 12px; }
.decision-dock {
  flex: none; border-top: 1px solid var(--border); padding: 12px 20px 14px; background: var(--panel-2);
}
.decision-dock .textarea { min-height: 0; margin-top: 8px; background: var(--panel); }
.decision-row {
  display: flex; justify-content: space-between; align-items: center; gap: 12px;
  flex-wrap: wrap; margin-top: 8px;
}
.decision-hint { font-size: 12px; line-height: 1.45; }
.history-comment { font-size: 13px; line-height: 1.55; }
.check-list { display: flex; flex-direction: column; gap: 2px; }
.auto-tag {
  font-style: normal; margin-left: auto; font-size: 11px; color: var(--success);
  background: rgba(18, 183, 106, 0.12); border-radius: 999px; padding: 0 6px; flex: none;
}
.detail-top { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; align-items: flex-start; }
.detail-identity { display: flex; gap: 12px; align-items: flex-start; }
.detail-name-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.detail-name { margin: 0; font-size: 18px; font-weight: 700; }
.detail-actions { display: flex; gap: 8px; flex-wrap: wrap; }

.meta-grid {
  display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px 16px;
  margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--border);
}
.meta-cell { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.meta-k { color: var(--muted); font-size: 12px; }
.meta-v { font-size: 13px; color: var(--text); }
.vis-pill {
  display: inline-flex; padding: 2px 8px; border-radius: 999px;
  background: var(--panel-2); border: 1px solid var(--border); font-size: 12px;
}

.checklist-box {
  margin-top: 14px; padding: 12px; background: #fafbfc; border: 1px solid var(--border); border-radius: 10px;
}
.check-title { font-size: 13px; font-weight: 650; margin-bottom: 8px; }
.check-item {
  display: flex; gap: 8px; align-items: flex-start; font-size: 13px; color: var(--text);
  margin: 0; padding: 3px 0; cursor: pointer;
}
.check-label { flex: 1; min-width: 0; }
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

.table-panel { padding: 0; overflow: visible; }
.skill-table { width: 100%; border-collapse: collapse; }
.skill-table th {
  text-align: left; padding: 12px 16px; font-size: 12px; font-weight: 500;
  color: var(--muted); background: #fafbfc; border-bottom: 1px solid var(--border);
}
.skill-table td {
  padding: 14px 16px; border-bottom: 1px solid var(--border); vertical-align: middle; font-size: 13px;
}
.skill-table tr:hover td { background: #fafbfc; }
.ops { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.policy-select { width: auto; max-width: 112px; padding: 2px 6px; font-size: 12px; }
.more-wrap { position: relative; }
.more-menu {
  position: absolute; right: 0; top: calc(100% + 4px); z-index: 8;
  min-width: 112px; background: #fff; border: 1px solid var(--border);
  border-radius: 8px; box-shadow: var(--shadow-lg); padding: 4px;
}
.more-menu button {
  display: block; width: 100%; text-align: left; background: none; border: none;
  padding: 6px 8px; cursor: pointer; color: var(--text); font-size: 13px; border-radius: 6px;
}
.more-menu button:hover { background: #fafbfc; }
.more-menu button.danger { color: var(--danger); }
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
  .audit-split { grid-template-columns: 1fr; height: auto; min-height: 0; }
  .audit-list { max-height: 420px; }
  .audit-detail { overflow: visible; max-height: none; }
  .detail-scroll { overflow: visible; }
  .meta-grid { grid-template-columns: 1fr 1fr; }
  .stats-band { grid-template-columns: 1fr; }
}
</style>
