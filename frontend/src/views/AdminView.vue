<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { authState } from '../stores/auth'
import { TYPE_LABELS, formatDate } from '../utils/format'
import StatusBadge from '../components/StatusBadge.vue'
import DebugCapabilityModal from '../components/DebugCapabilityModal.vue'

const tab = ref(new URLSearchParams(location.search).get('tab') || 'review')
const reviewQueue = ref([])
const allCaps = ref([])
const users = ref([])
const stats = ref(null)
const error = ref('')
const reviewComment = ref('')
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
  enabled: true
})
const gatewayTest = ref({})

const sections = [
  { key: 'review', label: '审核队列' },
  { key: 'caps', label: '能力管理' },
  { key: 'stats', label: '统计看板' },
  { key: 'users', label: '用户管理' },
  { key: 'gateway', label: 'MCP 网关' },
  { key: 'debug', label: '调试 / 试用' }
]

const sectionTitle = computed(() => sections.find((s) => s.key === tab.value)?.label || '管理后台')

const roleDefs = [
  { key: 'admin', label: '管理员', desc: '审核上架、下架归档、用户管理、统计看板、调试全部能力' },
  { key: 'user', label: '普通用户', desc: '所有登录用户都可发布/编辑/订阅能力；可调用调试自己创建或已加入的能力' }
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
    const [queue, caps, userList, stat, gatewayList] = await Promise.all([
      api.get('/admin/capabilities?status_filter=reviewing'),
      api.get('/admin/capabilities'),
      api.get('/admin/users'),
      api.get('/admin/stats'),
      api.get('/admin/mcp-gateway/servers')
    ])
    reviewQueue.value = queue
    allCaps.value = caps
    users.value = userList
    stats.value = stat
    gatewayServers.value = gatewayList
  } catch (e) {
    error.value = e.message
  }
}

async function review(cap, action) {
  try {
    await api.post(`/admin/capabilities/${cap.id}/review`, { action, comment: reviewComment.value })
    reviewComment.value = ''
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function statusAction(cap, action) {
  try {
    await api.post(`/admin/capabilities/${cap.id}/${action}`)
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

async function removeUser(u) {
  error.value = ''
  userNotice.value = ''
  if (!confirm(`确认删除用户「${u.username}」？\n该用户发布的能力将自动转移给当前管理员。此操作不可恢复。`)) return
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
  if (!confirm(`确认删除网关服务「${s.name}」？外部调用端点将立即失效。`)) return
  try {
    const r = await api.delete(`/admin/mcp-gateway/servers/${s.id}`)
    gatewayNotice.value = r.message
    await load()
  } catch (e) {
    error.value = e.message
  }
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

function gatewayUrl(s) {
  return `${location.origin}/api/mcp-gateway/${s.name}/stream`
}

function gatewaySseUrl(s) {
  return `${location.origin}/api/mcp-gateway/${s.name}/sse`
}

function copyText(text) {
  navigator.clipboard?.writeText(text).then(() => (gatewayNotice.value = '已复制到剪贴板'))
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
}

onMounted(load)
</script>

<template>
  <div class="admin-layout">
    <aside class="admin-side">
      <div class="side-title">管理后台</div>
      <button
        v-for="s in sections"
        :key="s.key"
        class="side-item"
        :class="{ active: tab === s.key }"
        @click="tab = s.key"
      >
        <span class="side-dot" :class="s.key"></span>{{ s.label }}
        <span v-if="s.key === 'review' && reviewQueue.length" class="side-count">{{ reviewQueue.length }}</span>
      </button>
      <div class="side-foot muted">AI 能力公共市场</div>
    </aside>

    <main class="admin-main">
      <div class="admin-header">
        <div>
          <h2 style="margin: 0">{{ sectionTitle }}</h2>
          <div class="muted" style="font-size: 13px">
            仅管理员可见：审核上架、下架归档、统计与用户治理
          </div>
        </div>
        <div v-if="stats" class="header-chips">
          <div class="chip"><span class="chip-num">{{ stats.total_capabilities }}</span>能力总数</div>
          <div class="chip"><span class="chip-num success">{{ stats.published_count }}</span>已上架</div>
          <div class="chip"><span class="chip-num warning">{{ stats.reviewing_count }}</span>待审核</div>
          <div class="chip"><span class="chip-num primary">{{ stats.total_usage }}</span>总用量</div>
        </div>
      </div>

      <div v-if="error" class="alert alert-error">{{ error }}</div>

      <section v-if="tab === 'review'">
        <div v-if="reviewQueue.length === 0" class="panel empty">没有待审核的能力</div>
        <div v-else class="panel">
          <div v-for="cap in reviewQueue" :key="cap.id" class="review-card">
            <div class="flex-between flex-wrap">
              <div>
                <router-link :to="`/capabilities/${cap.id}`" class="review-name">{{ cap.name }}</router-link>
                <span class="badge ml-8">{{ TYPE_LABELS[cap.type] }}</span>
                <span class="badge">v{{ cap.version }}</span>
              </div>
              <span class="muted">作者 {{ cap.author_name }} · {{ cap.organization }}</span>
            </div>
            <p class="muted">{{ cap.description || '无描述' }}</p>
            <div class="flex flex-wrap">
              <span v-for="t in cap.tags" :key="t" class="badge">{{ t }}</span>
              <span class="badge badge-primary">{{ cap.visibility }}</span>
            </div>
            <div class="flex mt-16">
              <input v-model="reviewComment" class="input" style="max-width: 360px" placeholder="审核意见（可选）" />
              <button class="btn btn-success btn-sm" @click="review(cap, 'approve')">通过并发布</button>
              <button class="btn btn-danger btn-sm" @click="review(cap, 'reject')">驳回</button>
              <button class="btn btn-sm" @click="review(cap, 'return')">打回修改</button>
            </div>
          </div>
        </div>
      </section>

      <section v-if="tab === 'caps'" class="panel">
        <table class="table">
          <thead>
            <tr><th>能力</th><th>类型</th><th>版本</th><th>状态</th><th>作者</th><th>使用量</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="cap in allCaps" :key="cap.id">
              <td><router-link :to="`/capabilities/${cap.id}`">{{ cap.name }}</router-link></td>
              <td>{{ TYPE_LABELS[cap.type] }}</td>
              <td>v{{ cap.version }}</td>
              <td><StatusBadge :status="cap.status" /></td>
              <td>{{ cap.author_name }}</td>
              <td>{{ cap.usage_count }}</td>
              <td>
                <div class="flex">
                  <button v-if="cap.status === 'published'" class="btn btn-sm" @click="statusAction(cap, 'deprecate')">下架</button>
                  <button v-if="['published', 'deprecated', 'rejected', 'returned'].includes(cap.status)" class="btn btn-sm btn-danger" @click="statusAction(cap, 'archive')">归档</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      <section v-if="tab === 'stats' && stats" class="mt-16">
        <div class="grid" style="grid-template-columns: 1fr 1fr">
          <div class="panel">
            <h3>类型分布</h3>
            <div v-for="t in Object.keys(stats.type_breakdown)" :key="t" class="type-bar">
              <span style="width: 130px">{{ TYPE_LABELS[t] }}</span>
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
          </div>
        </div>
        <div class="panel mt-16">
          <h3>最近使用</h3>
          <div v-for="(item, i) in stats.recent_usage" :key="i" class="recent-item">
            <div>{{ item.capability }}</div>
            <div class="muted" style="font-size: 12px">{{ item.action }} · {{ formatDate(item.at) }}</div>
          </div>
        </div>
      </section>

      <section v-if="tab === 'users'" class="panel">
        <div class="flex-between mb-16" style="align-items: flex-end">
          <div>
            <h3 style="margin: 0">角色定义</h3>
            <div class="role-cards mt-8">
              <div v-for="r in roleDefs" :key="r.key" class="role-card">
                <strong>{{ r.label }}</strong>
                <span class="muted" style="font-size: 12px">{{ r.desc }}</span>
              </div>
            </div>
          </div>
          <div class="flex" style="gap: 10px">
            <input v-model="userQuery" class="input" style="max-width: 200px" placeholder="搜索用户…" />
            <button class="btn btn-primary" @click="showCreateUser = true">+ 新增用户</button>
          </div>
        </div>
        <div v-if="userNotice" class="alert alert-success">{{ userNotice }}</div>
        <table class="table mt-16">
          <thead>
            <tr><th>用户</th><th>邮箱</th><th>组织</th><th>角色</th><th>状态</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="u in users" :key="u.id">
              <td>{{ u.display_name || u.username }} <span class="muted">@{{ u.username }}</span></td>
              <td>{{ u.email }}</td>
              <td>{{ u.organization || u.team || '-' }}</td>
              <td>
                <select :value="u.role === 'publisher' ? 'user' : u.role" class="select" style="width: auto; padding: 4px 8px" :disabled="isSelf(u)" @change="updateUser(u, { role: $event.target.value })">
                  <option value="admin">管理员</option>
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

      <section v-if="tab === 'gateway'" class="panel">
        <div class="flex-between mb-16" style="align-items: flex-end">
          <div>
            <h3 style="margin: 0">MCP HTTP 中转网关</h3>
            <p class="muted" style="font-size: 13px; max-width: 720px">
              把 stdio / HTTP / SSE 的 MCP 服务统一暴露为 HTTP 端点（Streamable HTTP + SSE），
              外部 MCP 客户端（Dify、Claude Desktop、其他 Agent）用令牌即可接入；
              市场 MCP 能力包也可以用 transport=gateway 引用这里的服务。
            </p>
          </div>
          <button class="btn btn-primary" @click="openGatewayCreate">+ 注册 MCP 服务</button>
        </div>
        <div v-if="gatewayNotice" class="alert alert-success">{{ gatewayNotice }}</div>
        <div v-if="gatewayServers.length === 0" class="empty">
          还没有注册 MCP 服务。点击右上角「+ 注册 MCP 服务」。
        </div>
        <table v-else class="table">
          <thead>
            <tr>
              <th>服务</th>
              <th>传输</th>
              <th>连接</th>
              <th>令牌</th>
              <th>状态</th>
              <th>外部端点</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in gatewayServers" :key="s.id">
              <td>
                <strong>{{ s.name }}</strong>
                <div class="muted" style="font-size: 11px">{{ s.description || '-' }}</div>
              </td>
              <td>
                <span class="badge badge-primary">{{ { stdio: 'stdio', http: 'HTTP', sse: 'SSE' }[s.transport] || s.transport }}</span>
              </td>
              <td>
                <code class="gw-code">{{ s.transport === 'stdio' ? `${s.command} ${(s.args || []).join(' ')}` : s.url }}</code>
              </td>
              <td>
                <span v-if="s.api_token" class="badge badge-warning">已启用</span>
                <span v-else class="badge">免鉴权</span>
              </td>
              <td>
                <span class="badge" :class="s.enabled ? 'badge-success' : 'badge-danger'">{{ s.enabled ? '启用' : '停用' }}</span>
              </td>
              <td>
                <div class="flex" style="gap: 6px">
                  <button class="btn btn-sm" @click="copyText(gatewayUrl(s))">复制 Stream</button>
                  <button class="btn btn-sm" @click="copyText(gatewaySseUrl(s))">复制 SSE</button>
                </div>
              </td>
              <td>
                <div class="flex" style="gap: 6px">
                  <button class="btn btn-sm" @click="testGateway(s)">测试连接</button>
                  <button class="btn btn-sm" @click="openGatewayEdit(s)">编辑</button>
                  <button class="btn btn-sm btn-danger" @click="removeGateway(s)">删除</button>
                </div>
              </td>
            </tr>
            <tr v-for="s in gatewayServers" v-if="gatewayTest[s.id]" :key="`t-${s.id}`">
              <td colspan="7">
                <div v-if="gatewayTest[s.id].loading" class="muted" style="font-size: 12px">连接测试中…</div>
                <div v-else class="flex-between flex-wrap">
                  <span class="badge" :class="gatewayTest[s.id].connected ? 'badge-success' : 'badge-danger'">
                    {{ gatewayTest[s.id].connected ? `已连接，发现 ${gatewayTest[s.id].tools.length} 个工具` : '连接失败' }}
                  </span>
                  <div class="flex" style="gap: 6px; flex-wrap: wrap">
                    <span v-for="t in (gatewayTest[s.id].tools || [])" :key="t.name" class="badge badge-primary">{{ t.name }}</span>
                  </div>
                  <span v-if="gatewayTest[s.id].error" class="muted" style="font-size: 12px">{{ gatewayTest[s.id].error }}</span>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      <section v-if="tab === 'debug'" class="panel">
        <p class="muted" style="font-size: 13px">
          调用 / 运行全部已发布能力（工具、Agent、技能、MCP、工作流），用于验证功能与参数，调用会计入用量。
        </p>
        <div class="flex mt-16" style="gap: 10px; flex-wrap: wrap">
          <select v-model="debugType" class="select" style="max-width: 160px" @change="selectDebugType(debugType)">
            <option v-for="t in ['tool', 'agent', 'skill', 'mcp', 'workflow', 'plugin']" :key="t" :value="t">{{ TYPE_LABELS[t] }}</option>
          </select>
          <select v-model="debugName" class="select" style="max-width: 320px">
            <option value="">选择已发布的能力…</option>
            <option v-for="c in debugCaps" :key="c.id" :value="c.name">{{ c.name }}（v{{ c.version }}）</option>
          </select>
          <button class="btn btn-primary" :disabled="!debugName" @click="openDebug">打开调试弹窗</button>
        </div>
      </section>
    </main>

    <DebugCapabilityModal :show="!!debugCap" :cap="debugCap" title="调试 / 试用" @close="debugCap = null" />

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
            外部连接：{{ location.origin }}/api/mcp-gateway/{{ gatewayForm.name || '…' }}/stream
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
.admin-layout { display: grid; grid-template-columns: 200px 1fr; gap: 24px; align-items: start; }
.admin-side {
  position: sticky; top: 78px; background: var(--panel); border: 1px solid var(--border);
  border-radius: 12px; padding: 12px; display: flex; flex-direction: column; gap: 2px;
}
.side-title { font-size: 13px; color: var(--muted); padding: 6px 10px 10px; }
.side-item {
  display: flex; align-items: center; gap: 8px; width: 100%; text-align: left;
  background: none; border: none; color: var(--muted); padding: 9px 10px; border-radius: 8px;
  font-size: 14px; cursor: pointer;
}
.side-item:hover { background: var(--panel-2); color: var(--text); }
.side-item.active { background: rgba(79,140,255,.12); color: var(--primary); }
.side-dot { width: 8px; height: 8px; border-radius: 3px; background: var(--border); flex: none; }
.side-dot.review { background: var(--warning); }
.side-dot.caps { background: var(--primary); }
.side-dot.stats { background: var(--success); }
.side-dot.users { background: #7a5cff; }
.side-dot.gateway { background: #ff9f43; }
.side-dot.debug { background: #ff9f43; }
.side-item.active .side-dot { background: var(--primary); }
.side-count {
  margin-left: auto; min-width: 18px; height: 18px; border-radius: 9px; display: inline-flex;
  align-items: center; justify-content: center; background: var(--danger); color: #fff; font-size: 11px; padding: 0 5px;
}
.gw-code {
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 11px;
  color: #9cc2ff;
  word-break: break-all;
}
.gw-modal { width: 680px; max-width: 100%; }
.checkbox { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--muted); padding-top: 9px; }
.code { font-family: 'Cascadia Code', Consolas, monospace; font-size: 12px; }
.side-foot { margin-top: 14px; padding: 8px 10px 2px; border-top: 1px solid var(--border); font-size: 12px; }
.admin-main { min-width: 0; }
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
  position: fixed; inset: 0; background: rgba(5, 8, 16, 0.72); z-index: 100;
  display: flex; align-items: center; justify-content: center; padding: 20px;
}
.modal { width: 640px; max-width: 100%; max-height: 90vh; overflow: auto; box-shadow: 0 24px 64px rgba(0,0,0,.55); }
.modal-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.modal-close { background: none; border: none; color: var(--muted); font-size: 16px; cursor: pointer; }
.field { display: flex; flex-direction: column; gap: 6px; }
.field label { font-size: 13px; color: var(--muted); }
.mt-12 { margin-top: 12px; }
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
.review-card { padding: 16px 0; border-bottom: 1px solid var(--border); }
.review-card:last-child { border-bottom: none; }
.review-name { font-size: 16px; font-weight: 600; margin-right: 8px; }
.ml-8 { margin-left: 8px; }
.type-bar { display: flex; align-items: center; gap: 12px; margin: 10px 0; font-size: 13px; }
.bar { flex: 1; height: 8px; background: var(--panel-2); border-radius: 4px; overflow: hidden; }
.bar-fill { height: 100%; background: linear-gradient(90deg, var(--primary), #7a5cff); border-radius: 4px; }
.top-item, .recent-item { display: flex; align-items: center; gap: 10px; padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
h3 { margin: 0 0 12px; }
@media (max-width: 900px) {
  .admin-layout { grid-template-columns: 1fr; }
  .admin-side { position: static; flex-direction: row; overflow-x: auto; }
}
</style>
