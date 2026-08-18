<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { authState } from '../stores/auth'
import {
  TYPE_LABELS,
  TYPE_CATEGORIES,
  VISIBILITY_LABELS,
  PACKAGE_HINTS,
  formatDate,
  stars,
  formatSize
} from '../utils/format'
import StatusBadge from '../components/StatusBadge.vue'

const props = defineProps({ id: { type: String, required: true } })
const router = useRouter()

const cap = ref(null)
const versions = ref([])
const ratings = ref([])
const error = ref('')
const notice = ref('')
const reviewComment = ref('')
const rating = ref({ score: 5, comment: '' })
const newVersion = ref('')
const uploading = ref(false)
const saving = ref(false)
const myIds = ref(new Set())
const myNotice = ref('')
const accessPolicy = ref('open')
const allowedUsers = ref('')
const accessSaved = ref('')
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
const canSubmit = computed(() => isOwner.value && ['draft', 'returned', 'rejected'].includes(cap.value?.status))
const canDelete = computed(() => isOwner.value && ['draft', 'returned', 'rejected', 'reviewing'].includes(cap.value?.status))
const canWithdraw = computed(() => isOwner.value && cap.value?.status === 'reviewing')
const canReview = computed(() => isAdmin.value && cap.value?.status === 'reviewing')
const isAgent = computed(() => cap.value?.type === 'agent')
const isPlugin = computed(() => cap.value?.type === 'plugin')
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
const hasSiblings = computed(() => (versions.value || []).some((v) => v.id !== cap.value?.id))
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

async function load() {
  try {
    cap.value = await api.get(`/capabilities/${props.id}`)
    accessPolicy.value = cap.value.access_policy || 'open'
    allowedUsers.value = (cap.value.allowed_users || []).join(', ')
    versions.value = await api.get(`/capabilities/${props.id}/versions`)
    ratings.value = await api.get(`/capabilities/${props.id}/ratings`)
  } catch (e) {
    error.value = e.message
  }
}

async function loadMy() {
  if (!authState.token) return
  try {
    const items = await api.get('/my/capabilities')
    myIds.value = new Set(items.map((c) => c.id))
  } catch {
    myIds.value = new Set()
  }
}

async function toggleMy() {
  myNotice.value = ''
  try {
    if (myIds.value.has(props.id)) {
      const r = await api.delete(`/my/capabilities/${props.id}`)
      const next = new Set(myIds.value)
      next.delete(props.id)
      myIds.value = next
      myNotice.value = r.message
    } else {
      const r = await api.post('/my/capabilities', { capability_id: props.id })
      myIds.value = new Set([...myIds.value, props.id])
      myNotice.value = r.message
    }
  } catch (e) {
    myNotice.value = e.message
  }
}

async function saveAccess() {
  accessSaved.value = ''
  try {
    await api.post(`/capabilities/${props.id}/access`, {
      access_policy: accessPolicy.value,
      allowed_users: allowedUsers.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
    })
    accessSaved.value = '调用权限已更新'
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

async function createVersion() {
  if (!newVersion.value) return
  try {
    const v = await api.post(`/publish/capabilities/${props.id}/versions`, { new_version: newVersion.value, change_type: 'patch' })
    notice.value = `新版本 v${v.version} 草稿已创建`
    newVersion.value = ''
    await load()
  } catch (e) {
    error.value = e.message
  }
}

function copyText(url) {
  navigator.clipboard?.writeText(url).then(() => (notice.value = '已复制到剪贴板'))
}

function cardUrl(id) {
  return `${location.origin}/api/a2a/agents/${id}/card`
}

function rpcUrl(id) {
  return `${location.origin}/api/a2a/agents/${id}/a2a`
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

onMounted(() => {
  load()
  loadMy()
})
</script>

<template>
  <div v-if="error && !cap" class="empty">{{ error }}</div>
  <div v-else-if="cap" class="detail">
    <div v-if="error" class="alert alert-error">{{ error }}</div>
    <div v-if="notice" class="alert alert-success">{{ notice }}</div>

    <div class="panel">
      <div class="flex-between flex-wrap">
        <div>
          <div class="flex">
            <h1 class="detail-name">{{ cap.name }}</h1>
            <StatusBadge :status="cap.status" />
            <span v-if="cap.latest" class="badge badge-primary">最新版</span>
          </div>
          <div class="muted mt-8">
            {{ TYPE_LABELS[cap.type] }} · v{{ cap.version }} · {{ VISIBILITY_LABELS[cap.visibility] }} ·
            作者 {{ cap.author_name || '-' }} · 更新于 {{ formatDate(cap.updated_at) }}
          </div>
        </div>
        <div class="detail-stats">
          <div><span class="num">{{ cap.usage_count }}</span><span class="muted">次使用</span></div>
          <div><span class="num" style="color: var(--warning)">{{ stars(cap.avg_rating) }}</span></div>
          <div><span class="num">{{ cap.rating_count }}</span><span class="muted">条评价</span></div>
        </div>
      </div>

      <p v-if="!canEdit" class="detail-desc">{{ cap.description || '暂无描述' }}</p>
      <div v-if="!canEdit" class="flex flex-wrap mt-8">
        <span v-if="cap.category" class="badge">{{ cap.category }}</span>
        <span v-for="t in cap.tags" :key="t" class="badge">{{ t }}</span>
      </div>

      <div v-if="canEdit" class="panel mt-24">
        <h3>编辑草稿</h3>
        <div class="muted" style="font-size: 13px">选错类型或名称时可直接改，无需删除重建。已有其他版本时类型/名称锁定。</div>
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
          <button class="btn btn-primary" :disabled="saving" @click="saveMeta">{{ saving ? '保存中…' : '保存修改' }}</button>
          <span v-if="!canChangeIdentity" class="muted" style="font-size: 12px; align-self: center">已有其他版本，类型和名称不可改</span>
        </div>
      </div>

      <div class="detail-actions mt-24">
        <div v-if="cap.status === 'published'" class="muted" style="font-size: 13px">
          调用 / 执行需授权：管理员、能力作者或已加入「我的能力」的调用方可以调用。
          <router-link v-if="authState.token" to="/my">加入我的能力后可在「我的能力」页调试</router-link>
        </div>
        <router-link v-if="cap.type === 'agent' && (isAdmin || isPublisher)" :to="`/agents/${encodeURIComponent(cap.name)}/edit`" class="btn">编辑 Agent</router-link>
        <router-link v-if="cap.type === 'skill' && (isOwner || isAdmin || isPublisher)" :to="`/skills/${encodeURIComponent(cap.name)}/edit`" class="btn">编辑技能</router-link>
        <router-link
          v-if="cap.type === 'workflow' && (canEdit || isAdmin || (authState.token && ['published', 'deprecated'].includes(cap.status)))"
          :to="`/workflows/${props.id}/edit`"
          class="btn"
        >
          🎨 {{ canEdit ? '可视化编辑' : '查看流程图' }}
        </router-link>
        <button
          v-if="authState.token && ['published', 'deprecated'].includes(cap.status)"
          class="btn"
          :class="myIds.has(cap.id) ? '' : 'btn-primary'"
          @click="toggleMy"
        >
          {{ myIds.has(cap.id) ? (isPlugin ? '移出插件' : '移出我的能力') : (isPlugin ? '一键加入插件' : '加入我的能力') }}
        </button>
        <button v-if="authState.token && cap.status === 'published'" class="btn" @click="subscribe">订阅更新</button>
        <span v-if="myNotice" class="muted" style="font-size: 12px; align-self: center">{{ myNotice }}</span>
        <button v-if="canSubmit" class="btn btn-success" @click="doAction(`/publish/capabilities/${props.id}/submit`)">提交审核</button>
        <button v-if="canWithdraw" class="btn" @click="withdrawReview">撤回审核</button>
        <button v-if="canDelete" class="btn btn-danger" @click="removeCap">删除</button>
        <button v-if="isAdmin && cap.status === 'published'" class="btn btn-danger" @click="doAction(`/admin/capabilities/${props.id}/deprecate`)">弃用</button>
        <button v-if="isAdmin && ['published', 'deprecated', 'rejected', 'returned'].includes(cap.status)" class="btn btn-danger" @click="doAction(`/admin/capabilities/${props.id}/archive`)">归档</button>
      </div>

      <div v-if="isPlugin" class="panel mt-24">
        <h3>插件组件</h3>
        <div class="muted" style="font-size: 13px">
          上传 plugin zip 后自动拆出；用户「一键加入」会同时加入下列组件。
        </div>
        <div v-if="pluginComponents.length === 0" class="muted mt-12">尚未上传插件包，或包内无组件</div>
        <table v-else class="table mt-12">
          <thead>
            <tr><th>角色</th><th>类型</th><th>名称</th><th>版本</th><th></th></tr>
          </thead>
          <tbody>
            <tr v-for="c in pluginComponents" :key="c.capability_id">
              <td><span class="badge">{{ c.role || '-' }}</span></td>
              <td>{{ TYPE_LABELS[c.type] || c.type }}</td>
              <td>{{ c.name }}</td>
              <td>v{{ c.version }}</td>
              <td>
                <router-link v-if="c.capability_id" :to="`/capabilities/${c.capability_id}`">查看</router-link>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="isAgent && (embeddedSkills.length || embeddedMcp.length)" class="panel mt-24">
        <h3>包含的 Skills / MCP</h3>
        <div class="muted" style="font-size: 13px">
          从 agent.json、skills/、mcp/ 提取；若市场已收录同名能力，可直接跳转。
        </div>
        <div v-if="embeddedSkills.length" class="mt-16">
          <h4 style="margin: 0 0 8px">Skills（{{ embeddedSkills.length }}）</h4>
          <table class="table">
            <thead>
              <tr><th>名称</th><th>描述</th><th>版本</th><th></th></tr>
            </thead>
            <tbody>
              <tr v-for="s in embeddedSkills" :key="s.name">
                <td>{{ s.display_name || s.name }}</td>
                <td class="muted">{{ s.description || '-' }}</td>
                <td>{{ s.version ? `v${s.version}` : '-' }}</td>
                <td>
                  <router-link v-if="s.capability_id" :to="`/capabilities/${s.capability_id}`">
                    市场已收录
                  </router-link>
                  <span v-else class="muted">-</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="embeddedMcp.length" class="mt-16">
          <h4 style="margin: 0 0 8px">MCP（{{ embeddedMcp.length }}）</h4>
          <table class="table">
            <thead>
              <tr><th>名称</th><th>描述</th><th>命令 / 包</th><th></th></tr>
            </thead>
            <tbody>
              <tr v-for="m in embeddedMcp" :key="m.name">
                <td>{{ m.name }}</td>
                <td class="muted">{{ m.description || '-' }}</td>
                <td class="muted">{{ m.command || m.package || '-' }}</td>
                <td>
                  <router-link v-if="m.capability_id" :to="`/capabilities/${m.capability_id}`">
                    市场已收录
                  </router-link>
                  <span v-else class="muted">-</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div v-if="cap.type === 'tool' && Object.keys(cap.input_schema || {}).length && !(cap.input_schema || {}).kind" class="panel mt-24">
        <h3>参数定义（schema.json）</h3>
        <table class="table">
          <thead>
            <tr><th>参数</th><th>类型</th><th>必填</th><th>说明</th></tr>
          </thead>
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

      <div v-if="isAgent && cap.status === 'published'" class="a2a-panel panel mt-24">
        <div class="flex-between flex-wrap">
          <h3>A2A 互调信息（Agent-to-Agent 协议）</h3>
          <span class="badge badge-primary">protocolVersion 1.0</span>
        </div>
        <div class="muted" style="font-size: 13px">
          本 Agent 可作为标准 A2A Agent 被发现与委派；tasks/send 属于外部调用，
          需要管理员授权令牌。
        </div>
        <div class="mt-16 a2a-urls">
          <div class="flex"><span class="muted" style="width: 120px">Agent Card</span>
            <code class="url-code">GET /api/a2a/agents/{{ cap.id }}/card</code>
            <button class="btn btn-sm" @click="copyText(cardUrl(cap.id))">复制</button>
          </div>
          <div class="flex mt-8"><span class="muted" style="width: 120px">JSON-RPC</span>
            <code class="url-code">POST /api/a2a/agents/{{ cap.id }}/a2a</code>
            <button class="btn btn-sm" @click="copyText(rpcUrl(cap.id))">复制</button>
          </div>
        </div>
      </div>

      <div v-if="(isOwner || isAdmin) && ['published', 'deprecated'].includes(cap.status)" class="panel mt-24">
        <div class="flex-between flex-wrap">
          <h3>调用权限</h3>
          <span v-if="accessSaved" class="muted" style="font-size: 12px">{{ accessSaved }}</span>
        </div>
        <div class="muted" style="font-size: 13px">
          运行配置，不占版本：控制哪些账号可以调用/调试该能力。
        </div>
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
          <button class="btn btn-primary" @click="saveAccess">保存</button>
        </div>
      </div>
    </div>

    <div class="grid mt-24" style="grid-template-columns: 1.4fr 1fr">
      <div class="panel">
        <h3>版本历史</h3>
        <table class="table">
          <thead><tr><th>版本</th><th>状态</th><th>更新时间</th><th></th></tr></thead>
          <tbody>
            <tr v-for="v in versions" :key="v.id">
              <td>v{{ v.version }}</td>
              <td><StatusBadge :status="v.status" /></td>
              <td class="muted">{{ formatDate(v.updated_at) }}</td>
              <td><router-link :to="`/capabilities/${v.id}`">查看</router-link></td>
            </tr>
          </tbody>
        </table>

        <div v-if="isOwner" class="mt-24">
          <h3>版本管理</h3>
          <div class="flex">
            <input v-model="newVersion" class="input" style="max-width: 180px" placeholder="如 1.1.0" />
            <button class="btn btn-primary" @click="createVersion">创建新版本</button>
          </div>
          <div class="muted mt-8" style="font-size: 12px">新版本以草稿创建，需重新提交审核。语义化版本：MAJOR.MINOR.PATCH</div>
        </div>

        <div v-if="canReview" class="mt-24">
          <h3>审核</h3>
          <textarea v-model="reviewComment" class="textarea" placeholder="审核意见（可选）"></textarea>
          <div class="flex mt-8">
            <button class="btn btn-success" @click="submitReview('approve')">通过并发布</button>
            <button class="btn btn-danger" @click="submitReview('reject')">驳回</button>
            <button class="btn" @click="submitReview('return')">打回修改</button>
          </div>
        </div>
      </div>

      <div>
        <div class="panel">
          <h3>能力包</h3>
          <div v-if="(cap.artifacts || []).length === 0" class="muted">尚未上传能力包</div>
          <div v-for="a in (cap.artifacts || [])" :key="a.id" class="artifact-item">
            <div>📦 {{ a.filename }}</div>
            <div class="muted" style="font-size: 12px">{{ formatSize(a.size_bytes) }} · {{ a.checksum.slice(0, 12) }}…</div>
          </div>
          <div v-if="isOwner && ['draft', 'returned', 'reviewing'].includes(cap.status)" class="mt-16">
            <label class="btn" :class="{ 'btn-primary': true }">
              {{ uploading ? '上传中…' : '上传能力包 (zip)' }}
              <input type="file" accept=".zip" style="display: none" @change="uploadArtifact" />
            </label>
            <div class="muted mt-8" style="font-size: 12px">
              包内需包含 {{ cap.type }} 规范要求文件（如 {{ PACKAGE_HINTS[cap.type] }}）
            </div>
          </div>
        </div>

        <div class="panel mt-24">
          <h3>评分与评论</h3>
          <div class="flex mb-16">
            <select v-model="rating.score" class="select" style="max-width: 90px">
              <option v-for="s in [5, 4, 3, 2, 1]" :key="s" :value="s">{{ s }} ★</option>
            </select>
            <input v-model="rating.comment" class="input" placeholder="写下你的评价" @keyup.enter="submitRating" />
            <button class="btn btn-primary btn-sm" @click="submitRating">提交</button>
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
      </div>
    </div>
  </div>
</template>

<style scoped>
.detail-name { margin: 0; font-size: 24px; }
.detail-desc { color: var(--muted); margin: 14px 0 0; max-width: 720px; }
.detail-stats { display: flex; gap: 20px; text-align: center; }
.detail-stats .num { display: block; font-size: 20px; font-weight: 600; }
.detail-actions { display: flex; gap: 10px; flex-wrap: wrap; }
.field { display: flex; flex-direction: column; gap: 6px; }
.field label { font-size: 13px; color: var(--muted); }
.runtime { border: 1px dashed var(--primary); }
h3 { margin: 0 0 12px; }
.artifact-item { padding: 8px 0; border-bottom: 1px solid var(--border); }
.rating-item { padding: 10px 0; border-bottom: 1px solid var(--border); }
.rating-item:last-child { border-bottom: none; }
.a2a-panel { border: 1px dashed #7a5cff; }
.url-code {
  background: var(--panel-2); padding: 4px 10px; border-radius: 6px;
  font-size: 12px; color: #9cc2ff; word-break: break-all;
}
.json-pre {
  background: var(--panel-2); border: 1px solid var(--border); border-radius: 8px;
  padding: 12px; font-size: 12px; overflow: auto; max-height: 260px; white-space: pre-wrap;
}
</style>
