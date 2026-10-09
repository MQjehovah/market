<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { formatDate } from '../utils/format'
import ConfirmActionModal from './ConfirmActionModal.vue'

/**
 * 治理后台 · 服务令牌域（自包含：自带状态/加载/确认/复制）。
 * 令牌用于 Agent / CI 以 Bearer mkt_svc_ 调能力网关与目录同步。
 */

const SCOPE_LABELS = {
  gateway: '能力网关',
  sync: '目录同步',
  runtime: '运行时（当前调不通）',
  admin: '管理（不会升权）'
}
const PRIMARY_SCOPES = [
  { key: 'gateway', label: '能力网关', desc: '经连接器网关调用已加入的能力' },
  { key: 'sync', label: '目录同步', desc: '拉取能力目录，给 Agent 或 CI 用' }
]
const EXTRA_SCOPES = [
  { key: 'runtime', label: '运行时', desc: '勾了也不会调通 /api/runtime，服务令牌会被拒绝' },
  { key: 'admin', label: '管理', desc: '不会把绑定账号变成管理员，管理接口仍看账号角色' }
]

const tokens = ref([])
const users = ref([])
const notice = ref('')
const error = ref('')
const showModal = ref(false)
const showCreated = ref(false)
const createdPlain = ref('')
const createdUser = ref('')
const copyState = ref('')
const form = ref({ name: '', user_id: '', scopes: ['gateway', 'sync'], expires_days: 365 })
const confirmAction = ref(null)

function isExpired(row) {
  if (!row?.expires_at || row.revoked) return false
  return new Date(row.expires_at).getTime() < Date.now()
}
function scopeLabel(key) {
  return SCOPE_LABELS[key] || key
}

const activeTokens = computed(() => tokens.value.filter((t) => !t.revoked && !isExpired(t)))
const expiredTokens = computed(() => tokens.value.filter((t) => !t.revoked && isExpired(t)))
const revokedTokens = computed(() => tokens.value.filter((t) => t.revoked))

async function load() {
  try {
    tokens.value = (await api.get('/admin/service-tokens')) || []
  } catch (e) {
    error.value = e.message
  }
}

async function openCreate() {
  form.value = { name: '', user_id: '', scopes: ['gateway', 'sync'], expires_days: 365 }
  error.value = ''
  notice.value = ''
  createdPlain.value = ''
  createdUser.value = ''
  copyState.value = ''
  showCreated.value = false
  showModal.value = true
  if (!users.value.length) {
    try {
      users.value = (await api.get('/admin/users')) || []
    } catch {
      users.value = []
    }
  }
}

function toggleScope(key) {
  const cur = form.value.scopes
  form.value.scopes = cur.includes(key) ? cur.filter((s) => s !== key) : [...cur, key]
}

async function createToken() {
  error.value = ''
  notice.value = ''
  const f = form.value
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
    const payload = {
      name: f.name.trim(),
      scopes: f.scopes,
      expires_days: Number.isFinite(days) && days > 0 ? days : null
    }
    if (f.user_id) payload.user_id = f.user_id
    const r = await api.post('/admin/service-tokens', payload)
    createdPlain.value = r.token || ''
    createdUser.value = r.username || ''
    copyState.value = ''
    showModal.value = false
    showCreated.value = true
    await load()
  } catch (e) {
    error.value = e.message
  }
}

function askRevoke(row) {
  notice.value = ''
  confirmAction.value = {
    kind: 'revoke-token',
    title: '确认吊销服务令牌？',
    body: `吊销「${row.name}」（${row.token_prefix}…）后，使用该令牌的调用将立即 401。`,
    okText: '吊销',
    danger: true,
    payload: row
  }
}

async function doRevoke() {
  const row = confirmAction.value?.payload
  confirmAction.value = null
  if (!row) return
  try {
    await api.post(`/admin/service-tokens/${row.id}/revoke`)
    notice.value = `已吊销「${row.name}」`
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function copyToken() {
  try {
    await navigator.clipboard.writeText(createdPlain.value)
    copyState.value = '已复制'
  } catch {
    copyState.value = '复制失败，请选中下面的文字手动复制'
  }
}

onMounted(load)
</script>

<template>
  <section>
    <div class="flex-between mb-16" style="align-items: flex-end">
      <p class="muted" style="font-size: 13px; max-width: 720px; margin: 0">
        给 Agent 或 CI 用的调用凭证，请求头是
        <code>Authorization: Bearer mkt_svc_…</code>
        。能做的事以勾选为准：能力网关、目录同步。这和 MCP 网关里的接入令牌不是同一种。明文只在创建时出现一次。
      </p>
      <button class="btn btn-primary" type="button" @click="openCreate">签发令牌</button>
    </div>
    <div v-if="notice" class="alert alert-success mb-12">{{ notice }}</div>
    <div v-if="error" class="alert alert-error mb-12">{{ error }}</div>

    <div v-if="tokens.length === 0" class="panel empty">
      还没有服务令牌。点击右上角「签发令牌」。
    </div>

    <template v-else>
      <h3 class="token-h">有效令牌（{{ activeTokens.length }}）</h3>
      <table v-if="activeTokens.length" class="table">
        <thead>
          <tr>
            <th>名称</th><th>前缀</th><th>绑定账号</th><th>权限</th><th>过期</th><th>最近使用</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in activeTokens" :key="t.id">
            <td><strong>{{ t.name }}</strong></td>
            <td><code class="token-prefix">{{ t.token_prefix }}…</code></td>
            <td>{{ t.username || t.user_id }}</td>
            <td>
              <div class="token-scopes">
                <span v-for="s in (t.scopes || [])" :key="s" class="badge badge-primary">{{ scopeLabel(s) }}</span>
              </div>
            </td>
            <td class="muted" style="font-size: 12px">{{ t.expires_at ? formatDate(t.expires_at) : '永不过期' }}</td>
            <td class="muted" style="font-size: 12px">{{ t.last_used_at ? formatDate(t.last_used_at) : '—' }}</td>
            <td><button class="btn btn-sm btn-danger" type="button" @click="askRevoke(t)">吊销</button></td>
          </tr>
        </tbody>
      </table>
      <div v-else class="muted mb-16" style="font-size: 13px">当前无有效令牌</div>

      <template v-if="expiredTokens.length">
        <h3 class="token-h">已过期（{{ expiredTokens.length }}）</h3>
        <p class="muted" style="font-size: 13px; margin: 0 0 8px">调用已经失败。可以吊销，避免和还能用的混在一起。</p>
        <table class="table">
          <thead>
            <tr><th>名称</th><th>前缀</th><th>绑定账号</th><th>权限</th><th>过期</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="t in expiredTokens" :key="t.id">
              <td>{{ t.name }}</td>
              <td><code class="token-prefix">{{ t.token_prefix }}…</code></td>
              <td>{{ t.username || t.user_id }}</td>
              <td><span v-for="s in (t.scopes || [])" :key="s" class="badge">{{ scopeLabel(s) }}</span></td>
              <td class="muted" style="font-size: 12px">{{ formatDate(t.expires_at) }}</td>
              <td><button class="btn btn-sm btn-danger" type="button" @click="askRevoke(t)">吊销</button></td>
            </tr>
          </tbody>
        </table>
      </template>

      <template v-if="revokedTokens.length">
        <h3 class="token-h muted">已吊销（{{ revokedTokens.length }}）</h3>
        <table class="table table-muted">
          <thead><tr><th>名称</th><th>前缀</th><th>绑定账号</th><th>权限</th><th>吊销时间</th></tr></thead>
          <tbody>
            <tr v-for="t in revokedTokens" :key="t.id">
              <td>{{ t.name }}</td>
              <td><code class="token-prefix">{{ t.token_prefix }}…</code></td>
              <td>{{ t.username || t.user_id }}</td>
              <td><span v-for="s in (t.scopes || [])" :key="s" class="badge">{{ scopeLabel(s) }}</span></td>
              <td class="muted" style="font-size: 12px">{{ t.revoked_at ? formatDate(t.revoked_at) : '—' }}</td>
            </tr>
          </tbody>
        </table>
      </template>
    </template>

    <div v-if="showModal" class="modal-mask" @click.self="showModal = false">
      <div class="modal panel" style="max-width: 520px">
        <div class="modal-header">
          <h3 style="margin: 0">签发服务令牌</h3>
          <button class="modal-close" type="button" @click="showModal = false">✕</button>
        </div>
        <div class="field">
          <label>名称 *</label>
          <input v-model="form.name" class="input" placeholder="如：零号员工生产 / CI 同步" />
        </div>
        <div class="field mt-12">
          <label>绑定账号</label>
          <select v-model="form.user_id" class="select">
            <option value="">按名称自动建立 svc_ 账号</option>
            <option v-for="u in users.filter((u) => u.is_active !== false)" :key="u.id" :value="u.id">
              {{ u.name || u.username }}（{{ u.username }}）
            </option>
          </select>
          <p class="muted" style="font-size: 12px; margin: 6px 0 0">不选时，同名令牌会继续用同一个 svc_ 账号。</p>
        </div>
        <div class="field mt-12">
          <label>权限 *</label>
          <div class="token-scope-list">
            <label v-for="opt in PRIMARY_SCOPES" :key="opt.key" class="token-scope-item">
              <input type="checkbox" :checked="form.scopes.includes(opt.key)" @change="toggleScope(opt.key)" />
              <span>
                <strong>{{ opt.label }}</strong>
                <span class="muted" style="font-size: 12px; display: block">{{ opt.desc }}</span>
              </span>
            </label>
          </div>
          <details class="mt-12">
            <summary class="muted" style="cursor: pointer; font-size: 13px">这些权限目前帮不上忙</summary>
            <div class="token-scope-list mt-12">
              <label v-for="opt in EXTRA_SCOPES" :key="opt.key" class="token-scope-item">
                <input type="checkbox" :checked="form.scopes.includes(opt.key)" @change="toggleScope(opt.key)" />
                <span>
                  <strong>{{ opt.label }}</strong>
                  <span class="muted" style="font-size: 12px; display: block">{{ opt.desc }}</span>
                </span>
              </label>
            </div>
          </details>
        </div>
        <div class="field mt-12">
          <label>有效天数。默认 365。填 0 表示不过期</label>
          <input v-model.number="form.expires_days" class="input" type="number" min="0" max="3650" />
        </div>
        <div v-if="error" class="alert alert-error mt-12">{{ error }}</div>
        <div class="modal-foot">
          <span class="muted" style="font-size: 12px">不选绑定账号时，会按名称建立 svc_ 账号</span>
          <div class="flex" style="gap: 10px">
            <button class="btn" type="button" @click="showModal = false">取消</button>
            <button class="btn btn-primary" type="button" @click="createToken">签发</button>
          </div>
        </div>
      </div>
    </div>

    <div v-if="showCreated" class="modal-mask">
      <div class="modal panel" style="max-width: 560px">
        <div class="modal-header">
          <h3 style="margin: 0">请保存令牌明文</h3>
        </div>
        <p class="muted" style="font-size: 13px; margin: 0 0 12px">
          关掉以后只能看到前缀。请先复制到密钥库或 Agent 配置。
          <template v-if="createdUser">绑定账号是 {{ createdUser }}。</template>
        </p>
        <code class="token-plain">{{ createdPlain }}</code>
        <p v-if="copyState" class="mt-12" :class="copyState === '已复制' ? 'alert alert-success' : 'alert alert-error'">{{ copyState }}</p>
        <div class="modal-foot">
          <span class="muted" style="font-size: 12px">请求头：Authorization: Bearer 加上面的令牌</span>
          <div class="flex" style="gap: 10px">
            <button class="btn btn-primary" type="button" @click="copyToken">{{ copyState === '已复制' ? '已复制' : '复制令牌' }}</button>
            <button class="btn" type="button" @click="showCreated = false">我已复制，关闭</button>
          </div>
        </div>
      </div>
    </div>

    <ConfirmActionModal
      :show="!!confirmAction"
      :title="confirmAction?.title || ''"
      :body="confirmAction?.body || ''"
      :ok-text="confirmAction?.okText || '确认'"
      :danger="!!confirmAction?.danger"
      @ok="doRevoke"
      @cancel="confirmAction = null"
    />
  </section>
</template>
