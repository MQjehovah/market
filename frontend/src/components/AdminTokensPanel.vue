<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { formatDate } from '../utils/format'
import ConfirmActionModal from './ConfirmActionModal.vue'

/**
 * 治理后台 · 服务令牌域（自包含：自带状态/加载/确认/复制）。
 * 令牌用于 Agent / CI / dashboard 调用 runtime、能力网关与目录同步。
 */

const TOKEN_SCOPE_OPTS = [
  { key: 'runtime', label: 'runtime', desc: '调用 /api/runtime/* 执行能力' },
  { key: 'gateway', label: 'gateway', desc: '经能力网关 relay 调用连接器' },
  { key: 'sync', label: 'sync', desc: '拉取能力目录 /api/capabilities/sync' },
  { key: 'admin', label: 'admin', desc: '管理接口（谨慎授予）' }
]

const tokens = ref([])
const notice = ref('')
const error = ref('')
const showModal = ref(false)
const showCreated = ref(false)
const createdPlain = ref('')
const form = ref({ name: '', scopes: ['runtime', 'gateway', 'sync'], expires_days: 365 })
const confirmAction = ref(null)

const activeTokens = computed(() => tokens.value.filter((t) => !t.revoked))
const revokedTokens = computed(() => tokens.value.filter((t) => t.revoked))

async function load() {
  try {
    tokens.value = (await api.get('/admin/service-tokens')) || []
  } catch (e) {
    error.value = e.message
  }
}

function openCreate() {
  form.value = { name: '', scopes: ['runtime', 'gateway', 'sync'], expires_days: 365 }
  error.value = ''
  notice.value = ''
  createdPlain.value = ''
  showCreated.value = false
  showModal.value = true
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
    const r = await api.post('/admin/service-tokens', {
      name: f.name.trim(),
      scopes: f.scopes,
      expires_days: Number.isFinite(days) && days > 0 ? days : null
    })
    createdPlain.value = r.token || ''
    showModal.value = false
    showCreated.value = true
    notice.value = '令牌已创建；明文仅此一次，请立即复制保存'
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

function copyToken() {
  navigator.clipboard?.writeText(createdPlain.value).then(() => {
    notice.value = '已复制到剪贴板'
  })
}

onMounted(load)
</script>

<template>
  <section>
    <div class="flex-between mb-16" style="align-items: flex-end">
      <p class="muted" style="font-size: 13px; max-width: 720px; margin: 0">
        服务令牌用于 Agent / CI / dashboard 以
        <code>Authorization: Bearer mkt_svc_…</code>
        调用 runtime、能力网关与目录同步。明文仅在创建时展示一次。
      </p>
      <button class="btn btn-primary" type="button" @click="openCreate">+ 签发令牌</button>
    </div>
    <div v-if="notice" class="alert alert-success mb-12">{{ notice }}</div>
    <div v-if="error" class="alert alert-error mb-12">{{ error }}</div>

    <div v-if="activeTokens.length === 0 && revokedTokens.length === 0" class="panel empty">
      还没有服务令牌。点击右上角「+ 签发令牌」。
    </div>

    <template v-else>
      <h3 class="token-h">有效令牌（{{ activeTokens.length }}）</h3>
      <table v-if="activeTokens.length" class="table">
        <thead>
          <tr>
            <th>名称</th><th>前缀</th><th>绑定账号</th><th>Scopes</th><th>过期</th><th>最近使用</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in activeTokens" :key="t.id">
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
            <td><button class="btn btn-sm btn-danger" type="button" @click="askRevoke(t)">吊销</button></td>
          </tr>
        </tbody>
      </table>
      <div v-else class="muted mb-16" style="font-size: 13px">当前无有效令牌</div>

      <template v-if="revokedTokens.length">
        <h3 class="token-h muted">已吊销（{{ revokedTokens.length }}）</h3>
        <table class="table table-muted">
          <thead><tr><th>名称</th><th>前缀</th><th>绑定账号</th><th>Scopes</th><th>创建时间</th></tr></thead>
          <tbody>
            <tr v-for="t in revokedTokens" :key="t.id">
              <td>{{ t.name }}</td>
              <td><code class="token-prefix">{{ t.token_prefix }}…</code></td>
              <td>{{ t.username || t.user_id }}</td>
              <td><span v-for="s in (t.scopes || [])" :key="s" class="badge">{{ s }}</span></td>
              <td class="muted" style="font-size: 12px">{{ formatDate(t.created_at) }}</td>
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
          <label>Scopes *</label>
          <div class="token-scope-list">
            <label v-for="opt in TOKEN_SCOPE_OPTS" :key="opt.key" class="token-scope-item">
              <input type="checkbox" :checked="form.scopes.includes(opt.key)" @change="toggleScope(opt.key)" />
              <span>
                <strong>{{ opt.label }}</strong>
                <span class="muted" style="font-size: 12px; display: block">{{ opt.desc }}</span>
              </span>
            </label>
          </div>
        </div>
        <div class="field mt-12">
          <label>有效天数（留空或 0 = 不设过期，建议 365）</label>
          <input v-model.number="form.expires_days" class="input" type="number" min="0" max="3650" />
        </div>
        <div v-if="error" class="alert alert-error mt-12">{{ error }}</div>
        <div class="modal-foot">
          <span class="muted" style="font-size: 12px">将自动创建/绑定 svc_* 服务账号</span>
          <div class="flex" style="gap: 10px">
            <button class="btn" type="button" @click="showModal = false">取消</button>
            <button class="btn btn-primary" type="button" @click="createToken">签发</button>
          </div>
        </div>
      </div>
    </div>

    <div v-if="showCreated" class="modal-mask" @click.self="showCreated = false">
      <div class="modal panel" style="max-width: 560px">
        <div class="modal-header">
          <h3 style="margin: 0">请保存令牌明文</h3>
          <button class="modal-close" type="button" @click="showCreated = false">✕</button>
        </div>
        <p class="muted" style="font-size: 13px; margin: 0 0 12px">
          关闭后无法再次查看完整令牌。请复制到密钥库或桌面/Agent 配置。
        </p>
        <code class="token-plain">{{ createdPlain }}</code>
        <div class="modal-foot">
          <span class="muted" style="font-size: 12px">用法：Authorization: Bearer &lt;token&gt;</span>
          <div class="flex" style="gap: 10px">
            <button class="btn btn-primary" type="button" @click="copyToken">复制令牌</button>
            <button class="btn" type="button" @click="showCreated = false">已保存，关闭</button>
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
