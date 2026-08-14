<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { authState } from '../stores/auth'
import { TYPE_LABELS, VISIBILITY_LABELS, formatDate, stars, formatSize } from '../utils/format'
import StatusBadge from '../components/StatusBadge.vue'

const props = defineProps({ id: { type: String, required: true } })

const cap = ref(null)
const versions = ref([])
const ratings = ref([])
const error = ref('')
const notice = ref('')
const reviewComment = ref('')
const rating = ref({ score: 5, comment: '' })
const runtimeParams = ref('{"city": "北京"}')
const task = ref('生成上月销售报表')
const context = ref('')
const mcpConfig = ref('{"transport": "stdio"}')
const workflowInput = ref('{"pattern": "**/*.py", "path": ""}')
const newVersion = ref('')
const uploading = ref(false)
const a2aInput = ref('生成上月销售报表')
const a2aResult = ref(null)
const a2aBusy = ref(false)
const lastInvokeResult = ref(null)

const isOwner = computed(() => cap.value && authState.user && cap.value.author_id === authState.user.id)
const isAdmin = computed(() => authState.user?.role === 'admin')
const isPublisher = computed(() => ['admin', 'publisher'].includes(authState.user?.role))

const canEdit = computed(() => isOwner.value && ['draft', 'returned'].includes(cap.value?.status))
const canSubmit = computed(() => isOwner.value && ['draft', 'returned', 'rejected'].includes(cap.value?.status))
const canReview = computed(() => isAdmin.value && cap.value?.status === 'reviewing')
const isAgent = computed(() => cap.value?.type === 'agent')

async function load() {
  try {
    cap.value = await api.get(`/capabilities/${props.id}`)
    versions.value = await api.get(`/capabilities/${props.id}/versions`)
    ratings.value = await api.get(`/capabilities/${props.id}/ratings`)
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

async function runtimeAction() {
  notice.value = ''
  error.value = ''
  const name = encodeURIComponent(cap.value.name)
  try {
    let path = ''
    let payload = {}
    if (cap.value.type === 'agent') {
      path = `/runtime/agents/${name}/tasks`
      payload = { task: task.value }
    } else if (cap.value.type === 'tool') {
      path = `/runtime/tools/${name}/invoke`
      payload = { params: JSON.parse(runtimeParams.value || '{}') }
    } else if (cap.value.type === 'skill') {
      path = `/runtime/skills/${name}/activate`
      payload = { context: context.value }
    } else if (cap.value.type === 'workflow') {
      path = `/runtime/workflows/${name}/executions`
      payload = { input: JSON.parse(workflowInput.value || '{}') }
    } else {
      path = `/runtime/mcp/${name}/install`
      payload = { config: JSON.parse(mcpConfig.value || '{}') }
    }
    const result = await api.post(path, payload)
    lastInvokeResult.value = result
    notice.value = `${result.message}（已记录用量）`
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function sendA2ATask() {
  a2aResult.value = null
  a2aBusy.value = true
  try {
    const payload = {
      jsonrpc: '2.0',
      id: `web-${Date.now()}`,
      method: 'tasks/send',
      params: {
        id: `web-task-${Date.now()}`,
        message: { role: 'user', parts: [{ type: 'text', text: a2aInput.value }] },
        metadata: { source: 'portal' }
      }
    }
    const body = await api.post(`/a2a/agents/${props.id}/a2a`, payload)
    a2aResult.value = body
  } catch (e) {
    a2aResult.value = { error: { message: e.message } }
  } finally {
    a2aBusy.value = false
  }
}

function textOf(parts) {
  return (parts || []).filter((p) => p.type === 'text' && p.text).map((p) => p.text).join('\n')
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

const actionLabel = computed(() => ({
  agent: '执行任务',
  tool: '调用工具',
  skill: '激活技能',
  mcp: '安装 MCP'
}[cap.value?.type] || (cap.value?.type === 'workflow' ? '执行工作流' : '执行')))

onMounted(load)
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

      <p class="detail-desc">{{ cap.description || '暂无描述' }}</p>
      <div class="flex flex-wrap mt-8">
        <span v-if="cap.category" class="badge">{{ cap.category }}</span>
        <span v-for="t in cap.tags" :key="t" class="badge">{{ t }}</span>
      </div>

      <div class="detail-actions mt-24">
        <button v-if="authState.token && cap.status === 'published'" class="btn btn-primary" @click="runtimeAction">{{ actionLabel }}</button>
        <router-link v-if="cap.type === 'agent' && (isAdmin || isPublisher)" :to="`/agents/${encodeURIComponent(cap.name)}/edit`" class="btn">编辑 Agent</router-link>
        <button v-if="authState.token && cap.status === 'published'" class="btn" @click="subscribe">订阅更新</button>
        <button v-if="canSubmit" class="btn btn-success" @click="doAction(`/publish/capabilities/${props.id}/submit`)">提交审核</button>
        <button v-if="isAdmin && cap.status === 'published'" class="btn btn-danger" @click="doAction(`/admin/capabilities/${props.id}/deprecate`)">弃用</button>
        <button v-if="isAdmin && ['published', 'deprecated', 'rejected', 'returned'].includes(cap.status)" class="btn btn-danger" @click="doAction(`/admin/capabilities/${props.id}/archive`)">归档</button>
      </div>

      <div v-if="cap.status === 'published'" class="runtime panel mt-24">
        <h3>执行引擎 · 使用演示</h3>
        <template v-if="cap.type === 'agent'">
          <div class="field"><label>任务描述</label><input v-model="task" class="input" /></div>
        </template>
        <template v-else-if="cap.type === 'tool'">
          <div class="field"><label>参数 JSON</label><textarea v-model="runtimeParams" class="textarea" rows="4"></textarea></div>
        </template>
        <template v-else-if="cap.type === 'skill'">
          <div class="field"><label>任务上下文</label><input v-model="context" class="input" placeholder="如：写测试" /></div>
        </template>
        <template v-else-if="cap.type === 'mcp'">
          <div class="field"><label>连接配置 JSON</label><textarea v-model="mcpConfig" class="textarea" rows="4"></textarea></div>
        </template>
        <template v-else-if="cap.type === 'workflow'">
          <div class="field"><label>工作流入参 JSON</label><textarea v-model="workflowInput" class="textarea" rows="4"></textarea></div>
        </template>
        <button class="btn btn-primary" @click="runtimeAction">{{ actionLabel }}</button>
        <div v-if="cap.type === 'tool' && lastInvokeResult" class="invoke-result mt-16">
          <h4>执行结果</h4>
          <div v-if="lastInvokeResult.result?.status === 'ok'" class="alert alert-success">
            {{ lastInvokeResult.message }}
          </div>
          <div v-else class="alert alert-error">{{ lastInvokeResult.message }}</div>
          <pre class="json-pre">{{ JSON.stringify(lastInvokeResult.result, null, 2) }}</pre>
        </div>
      </div>

      <div v-if="cap.type === 'tool' && Object.keys(cap.input_schema || {}).length" class="panel mt-24">
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
          <h3>A2A 互调（Agent-to-Agent 协议）</h3>
          <span class="badge badge-primary">protocolVersion 1.0</span>
        </div>
        <div class="muted" style="font-size: 13px">
          其他 Agent 可通过标准 A2A JSON-RPC 发现并委派任务给本 Agent。
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
        <div class="flex mt-16">
          <input v-model="a2aInput" class="input" placeholder="输入委派的任务内容" @keyup.enter="sendA2ATask" />
          <button class="btn btn-primary" :disabled="a2aBusy" @click="sendA2ATask">
            {{ a2aBusy ? '委派中…' : '发送任务 (tasks/send)' }}
          </button>
        </div>
        <div v-if="a2aResult" class="a2a-result mt-16">
          <div v-if="a2aResult.error" class="alert alert-error">{{ a2aResult.error.message }}（code {{ a2aResult.error.code }}）</div>
          <template v-else>
            <div class="flex" style="gap: 8px">
              <span class="badge" :class="a2aResult.result?.status?.state === 'completed' ? 'badge-success' : 'badge-warning'">
                state: {{ a2aResult.result?.status?.state }}
              </span>
              <span class="muted" style="font-size: 12px">task id: {{ a2aResult.result?.id }}</span>
            </div>
            <pre class="json-pre mt-8">{{ textOf(a2aResult.result?.status?.message?.parts) }}</pre>
          </template>
        </div>
      </div>
    </div>

        <div v-if="cap.type === 'agent' && lastInvokeResult" class="invoke-result mt-16">
          <h4>执行结果（mode: {{ lastInvokeResult.mode }}）</h4>
          <div class="alert" :class="lastInvokeResult.mode === 'llm' ? 'alert-success' : 'alert-warning'">{{ lastInvokeResult.output }}</div>
          <pre class="json-pre">{{ JSON.stringify({ tool_calls: lastInvokeResult.tool_calls, runtime: lastInvokeResult.runtime }, null, 2) }}</pre>
        </div>
        <div v-if="cap.type === 'workflow' && lastInvokeResult" class="invoke-result mt-16">
          <h4>执行结果</h4>
          <div class="alert" :class="lastInvokeResult.state === 'succeeded' ? 'alert-success' : 'alert-error'">
            state: {{ lastInvokeResult.state }}{{ lastInvokeResult.error ? ' · ' + lastInvokeResult.error : '' }}
          </div>
          <pre class="json-pre">{{ JSON.stringify({ node_states: lastInvokeResult.node_states, outputs: lastInvokeResult.outputs }, null, 2) }}</pre>
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
              包内需包含 {{ cap.type }} 规范要求文件（如 {{ cap.type === 'agent' ? 'agent.json + PROMPT.md' : cap.type === 'tool' ? 'tool.json + schema.json + implementation/tool.py' : cap.type === 'skill' ? 'skill.json + SKILL.md' : 'mcp.json + connection.json + tools.json + security.json' }}）
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
.invoke-result h4 { margin: 0 0 8px; }
</style>
