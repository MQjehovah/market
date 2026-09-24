<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import { TYPE_LABELS, formatDate } from '../utils/format'
import ConfirmActionModal from '../components/ConfirmActionModal.vue'

const secrets = ref([])
const caps = ref([])
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const notice = ref('')
const confirmDelete = ref(null)

const form = reactive({
  key_name: '',
  value: '',
  scope: '',
  label: ''
})

function requiredEnvFor(cap) {
  const schema = cap?.input_schema
  if (schema && schema.kind === 'mcp' && Array.isArray(schema.required_env)) {
    return schema.required_env.map(String).filter(Boolean)
  }
  if (schema?.env && typeof schema.env === 'object') {
    return Object.keys(schema.env)
  }
  const dash = cap?.consumers?.dashboard
  if (dash?.required_env?.length) return dash.required_env.map(String)
  return []
}

const mcpCapsNeedingEnv = computed(() =>
  caps.value
    .filter((c) => c.type === 'mcp' && (c.added || c.owned) && c.status === 'published')
    .map((c) => {
      const keys = requiredEnvFor(c)
      return { ...c, requiredKeys: keys }
    })
    .filter((c) => c.requiredKeys.length > 0)
)

const capNameById = computed(() => {
  const m = {}
  for (const c of caps.value) m[c.id] = c.name
  return m
})

function scopeLabel(scope) {
  if (!scope) return '全局'
  return capNameById.value[scope] || `能力 ${scope.slice(0, 8)}…`
}

function filledKeysFor(capId, keys) {
  const set = new Set()
  for (const s of secrets.value) {
    if (!keys.includes(s.key_name)) continue
    if (s.scope === capId || (!s.scope && !capId)) set.add(s.key_name)
  }
  // 能力级：全局也算已填（解析时全局可用）
  if (capId) {
    for (const s of secrets.value) {
      if (!s.scope && keys.includes(s.key_name)) set.add(s.key_name)
    }
    for (const s of secrets.value) {
      if (s.scope === capId && keys.includes(s.key_name)) set.add(s.key_name)
    }
  }
  return [...set]
}

function missingKeysFor(cap) {
  const filled = new Set(filledKeysFor(cap.id, cap.requiredKeys))
  return cap.requiredKeys.filter((k) => !filled.has(k))
}

const pendingCaps = computed(() =>
  mcpCapsNeedingEnv.value
    .map((c) => ({
      ...c,
      filled: filledKeysFor(c.id, c.requiredKeys),
      missing: missingKeysFor(c)
    }))
    .filter((c) => c.missing.length > 0)
)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [sec, mine] = await Promise.all([
      api.get('/my/secrets'),
      api.get('/my/capabilities?scope=all')
    ])
    secrets.value = sec || []
    caps.value = mine || []
  } catch (e) {
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function saveOne() {
  error.value = ''
  notice.value = ''
  if (!form.key_name.trim() || !form.value) {
    error.value = '请填写密钥名和值'
    return
  }
  saving.value = true
  try {
    await api.put('/my/secrets', {
      key_name: form.key_name.trim(),
      value: form.value,
      scope: form.scope || '',
      label: form.label || ''
    })
    form.value = ''
    notice.value = '已加密保存（列表不回显明文）'
    await load()
  } catch (e) {
    error.value = e.message || '保存失败'
  } finally {
    saving.value = false
  }
}

async function doDelete(row) {
  confirmDelete.value = null
  try {
    await api.delete(`/my/secrets/${row.id}`)
    notice.value = `已删除 ${row.key_name}`
    await load()
  } catch (e) {
    error.value = e.message || '删除失败'
  }
}

onMounted(load)
</script>

<template>
  <div class="secrets-page">
    <div class="page-head">
      <div>
        <h1 class="page-title">全局共享密钥</h1>
        <p class="page-desc muted">
          跨能力共享的托管凭据（如 ERP / 域账号），加密存库，平台轨与线上试用按当前用户自动注入；列表永不回显明文。
          单能力专用凭据请在对应能力详情页「环境变量 / 业务凭据」直接配置。
        </p>
      </div>
      <router-link to="/my" class="btn">我的能力</router-link>
    </div>

    <div v-if="notice" class="alert alert-success mb-16">{{ notice }}</div>
    <div v-if="error" class="alert alert-error mb-16">{{ error }}</div>

    <div v-if="pendingCaps.length" class="panel mb-16">
      <h2 class="section-title">待补齐</h2>
      <p class="muted" style="font-size: 13px; margin: 0 0 12px">
        已加入/发布的连接器仍缺托管密钥。请到能力详情页「环境变量 / 业务凭据」填写，保存后平台轨自动注入。
      </p>
      <div class="pending-list">
        <div v-for="cap in pendingCaps" :key="cap.id" class="pending-row">
          <div>
            <router-link :to="`/capabilities/${cap.id}`">{{ cap.name }}</router-link>
            <span class="badge" style="margin-left: 8px">{{ TYPE_LABELS.mcp }}</span>
            <div class="muted" style="font-size: 12px; margin-top: 4px">
              缺
              <code v-for="k in cap.missing" :key="k" style="margin-right: 6px">{{ k }}</code>
            </div>
          </div>
          <router-link class="btn btn-sm btn-primary" :to="`/capabilities/${cap.id}`">去配置</router-link>
        </div>
      </div>
    </div>

    <div class="grid secrets-layout">
      <div class="panel">
        <h2 class="section-title">新增 / 更新</h2>
        <div class="field">
          <label>密钥名（环境变量）</label>
          <input v-model="form.key_name" class="input" placeholder="ERP_API_KEY" autocomplete="off" />
        </div>
        <div class="field">
          <label>值</label>
          <input
            v-model="form.value"
            class="input"
            type="password"
            placeholder="明文仅本次提交，落库加密"
            autocomplete="new-password"
          />
        </div>
        <div class="field">
          <label>作用域</label>
          <select v-model="form.scope" class="input">
            <option value="">全局（所有能力可用）</option>
            <option v-for="c in mcpCapsNeedingEnv" :key="c.id" :value="c.id">
              {{ c.name }}
            </option>
          </select>
        </div>
        <div class="field">
          <label>备注（可选）</label>
          <input v-model="form.label" class="input" placeholder="生产 ERP" autocomplete="off" />
        </div>
        <button class="btn btn-primary" type="button" :disabled="saving" @click="saveOne">
          {{ saving ? '保存中…' : '加密保存' }}
        </button>
      </div>

      <div class="panel table-panel">
        <h2 class="section-title" style="padding: 16px 16px 0">已托管</h2>
        <div v-if="loading" class="empty">加载中…</div>
        <div v-else-if="!secrets.length" class="empty">还没有托管密钥。从上方表单添加，或从「待补齐」按能力填写。</div>
        <table v-else class="table">
          <thead>
            <tr>
              <th>密钥名</th>
              <th>作用域</th>
              <th>备注</th>
              <th>更新</th>
              <th style="width: 80px" />
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in secrets" :key="row.id">
              <td><code>{{ row.key_name }}</code></td>
              <td>
                <span v-if="!row.scope" class="badge">全局</span>
                <router-link v-else :to="`/capabilities/${row.scope}`">{{ scopeLabel(row.scope) }}</router-link>
              </td>
              <td class="muted">{{ row.label || '—' }}</td>
              <td class="muted">{{ formatDate(row.updated_at) }}</td>
              <td>
                <button class="btn btn-sm btn-danger" type="button" @click="confirmDelete = row">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <ConfirmActionModal
      :show="!!confirmDelete"
      title="删除托管密钥"
      :body="confirmDelete ? `确定删除 ${confirmDelete.key_name}？删除后平台轨将无法注入该项。` : ''"
      ok-text="删除"
      :danger="true"
      @ok="doDelete(confirmDelete)"
      @cancel="confirmDelete = null"
    />
  </div>
</template>

<style scoped>
.page-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 20px;
}
.page-title { margin: 0 0 6px; font-size: 22px; }
.page-desc { margin: 0; font-size: 13px; max-width: 640px; line-height: 1.5; }
.section-title { margin: 0 0 12px; font-size: 16px; }
.secrets-layout {
  grid-template-columns: 320px 1fr;
  gap: 16px;
  align-items: start;
}
.field { margin-bottom: 12px; }
.field label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.pending-list { display: flex; flex-direction: column; gap: 10px; }
.pending-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  background: var(--panel-2);
  border-radius: 8px;
}
.table-panel { padding: 0; overflow: hidden; }

.table-panel .table { margin: 0; }
.mb-16 { margin-bottom: 16px; }
@media (max-width: 900px) {
  .secrets-layout { grid-template-columns: 1fr; }
}
</style>
