<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import StatusBadge from '../components/StatusBadge.vue'
import MarkdownEditor from '../components/MarkdownEditor.vue'
import AiDraftPanel from '../components/AiDraftPanel.vue'
import { bindUnsavedGuard } from '../utils/unsaved'

const route = useRoute()
const agentName = computed(() => route.params.name)
const loading = ref(false)
const error = ref('')
const notice = ref('')
const saved = ref(null)
const baseVersion = ref('')
const catalog = ref({ tool: [], skill: [], mcp: [] })
const baseline = ref(null)

function formSnap() {
  return JSON.stringify({
    prompt: form.prompt,
    description: form.description,
    category: form.category,
    tagsText: form.tagsText,
    newVersion: form.newVersion,
    deps: form.deps
  })
}

function markClean() {
  baseline.value = formSnap()
}

bindUnsavedGuard(() => baseline.value !== null && formSnap() !== baseline.value)

const TYPE_LABELS = { tool: '工具', skill: '技能', mcp: '连接器' }

const form = reactive({
  prompt: '',
  description: '',
  category: '',
  tagsText: '',
  newVersion: '',
  deps: []
})

function newDepRow() {
  return { type: 'tool', name: '', version: '' }
}

function depCaps(type) {
  return catalog.value[type] || []
}

async function loadCatalog() {
  const [t, s, m] = await Promise.all([
    api.get('/capabilities?type=tool&status=published&page_size=100'),
    api.get('/capabilities?type=skill&status=published&page_size=100'),
    api.get('/capabilities?type=mcp&status=published&page_size=100')
  ])
  catalog.value = { tool: t.items, skill: s.items, mcp: m.items }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const body = await api.get(`/agents/${encodeURIComponent(agentName.value)}/edit`)
    saved.value = body.capability
    baseVersion.value = body.base_version || ''
    form.prompt = body.prompt
    form.description = body.capability.description || ''
    form.category = body.capability.category || ''
    form.tagsText = (body.capability.tags || []).join(',')
    form.deps = (body.dependencies || []).map((d) => ({ ...d }))
    if (!form.deps.length) form.deps = [newDepRow()]
    markClean()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function save() {
  error.value = ''
  notice.value = ''
  if (!form.prompt.trim()) {
    error.value = '提示词不能为空'
    return
  }
  const payload = {
    prompt: form.prompt,
    description: form.description,
    category: form.category,
    tags: form.tagsText.split(',').map((s) => s.trim()).filter(Boolean),
    new_version: form.newVersion,
    dependencies: form.deps.filter((d) => d.name)
  }
  try {
    const body = await api.put(`/agents/${encodeURIComponent(agentName.value)}/edit`, payload)
    saved.value = body.capability
    baseVersion.value = body.base_version || ''
    form.prompt = body.prompt
    form.deps = (body.dependencies || []).map((d) => ({ ...d }))
    if (!form.deps.length) form.deps = [newDepRow()]
    markClean()
    notice.value =
      body.capability.status === 'draft'
        ? `已保存为 v${body.capability.version} 草稿，提交审核后发布`
        : '已保存'
  } catch (e) {
    error.value = e.message
  }
}

async function submitReview() {
  error.value = ''
  try {
    saved.value = await api.post(`/publish/capabilities/${saved.value.id}/submit`)
    notice.value = '已提交审核'
  } catch (e) {
    error.value = e.message
  }
}

async function exportSnapshot() {
  error.value = ''
  notice.value = ''
  const deps = form.deps.filter((d) => d.name)
  try {
    const cap = await api.post('/assemble/agents', {
      persona: agentName.value,
      name: `${agentName.value}-快照`,
      version: '0.1.0',
      description: '快照导出',
      dependencies: deps
    })
    notice.value = `已导出快照包（草稿）：${cap.id}`
  } catch (e) {
    error.value = e.message
  }
}

function onAi(fields) {
  if (fields.prompt) form.prompt = fields.prompt
  if (fields.description) form.description = fields.description
  if (Array.isArray(fields.tags) && fields.tags.length) form.tagsText = fields.tags.join(',')
}

onMounted(() => {
  loadCatalog()
  load()
})
</script>

<template>
  <div>
    <div v-if="error && !saved" class="empty">{{ error }}</div>
    <div v-else class="panel">
      <div class="flex-between flex-wrap">
        <div>
          <div class="flex" style="gap: 10px">
            <h2>编辑专家：{{ agentName }}</h2>
            <StatusBadge :status="saved?.status" />
          </div>
          <div class="muted" style="font-size: 13px">
            当前编辑版本 v{{ saved?.version }} · 最新已发布 v{{ baseVersion || '—' }}
          </div>
        </div>
        <div class="flex" style="gap: 10px">
          <router-link class="btn" :to="`/capabilities/${saved?.id}`">查看详情</router-link>
        </div>
      </div>

      <div v-if="error" class="alert alert-error">{{ error }}</div>
      <div v-if="notice" class="alert alert-success">{{ notice }}</div>

      <AiDraftPanel
        kind="agent"
        :name="agentName"
        hint="例如：一个处理售后退换货的专家，能查工单、判定责任、写回工单评论并通知负责人"
        @apply="onAi"
      />

      <div class="field mt-16">
        <label>提示词（PROMPT.md）</label>
        <MarkdownEditor v-model="form.prompt" />
      </div>

      <div class="panel mt-16">
        <h3>绑定能力（工具 / 技能 / 连接器）</h3>
        <div v-for="(dep, i) in form.deps" :key="i" class="dep-row flex">
          <select v-model="dep.type" class="select" style="max-width: 110px">
            <option value="tool">工具</option>
            <option value="skill">技能</option>
            <option value="mcp">连接器</option>
          </select>
          <select v-model="dep.name" class="select" style="flex: 1">
            <option value="">选择能力…</option>
            <option v-for="c in depCaps(dep.type)" :key="c.id" :value="c.name">
              {{ c.name }} v{{ c.version }}{{ (c.tags || []).includes('plugin-component') ? ' · 来自能力包' : '' }}
            </option>
          </select>
          <input v-model="dep.version" class="input" style="max-width: 130px" placeholder="版本(留空=最新)" />
          <button class="btn btn-sm btn-danger" @click="form.deps.splice(i, 1)">移除</button>
        </div>
        <button class="btn btn-sm mt-8" @click="form.deps.push(newDepRow())">+ 添加绑定</button>
      </div>

      <div class="grid mt-16" style="grid-template-columns: 1fr 1fr">
        <div class="field"><label>描述</label><input v-model="form.description" class="input" /></div>
        <div class="field"><label>分类</label><input v-model="form.category" class="input" placeholder="如：垂直领域" /></div>
        <div class="field"><label>标签（逗号分隔）</label><input v-model="form.tagsText" class="input" placeholder="如：运维,客服" /></div>
        <div class="field">
          <label>新版本号（留空自动基于最新发布 +1 patch）</label>
          <input v-model="form.newVersion" class="input" placeholder="如：1.1.0" />
        </div>
      </div>

      <div class="flex mt-16" style="gap: 12px">
        <button class="btn btn-primary" :disabled="loading" @click="save">
          {{ saved?.status === 'draft' ? '保存草稿' : '保存为新版本草稿' }}
        </button>
        <button v-if="saved?.status === 'draft'" class="btn" @click="submitReview">提交审核</button>
        <button class="btn" @click="exportSnapshot">导出快照包</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dep-row { gap: 10px; margin-bottom: 10px; }
.field { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
.field label { font-size: 13px; color: var(--muted); }
</style>
