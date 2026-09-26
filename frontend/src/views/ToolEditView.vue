<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import { formatSize } from '../utils/format'
import { bindUnsavedGuard } from '../utils/unsaved'
import StatusBadge from '../components/StatusBadge.vue'
import CodeEditor from '../components/CodeEditor.vue'
import AiDraftPanel from '../components/AiDraftPanel.vue'

const route = useRoute()
const name = route.params.name

const data = ref(null)
const schemaText = ref('{}')
const implementation = ref('')
const description = ref('')
const tags = ref('')
const error = ref('')
const notice = ref('')
const saving = ref(false)
const baseline = ref(null)

function formSnap() {
  return JSON.stringify({
    schemaText: schemaText.value,
    implementation: implementation.value,
    description: description.value,
    tags: tags.value
  })
}

function markClean() {
  baseline.value = formSnap()
}

bindUnsavedGuard(() => baseline.value !== null && formSnap() !== baseline.value)

const schemaError = computed(() => {
  try {
    const v = JSON.parse(schemaText.value || '{}')
    if (v === null || typeof v !== 'object' || Array.isArray(v)) return 'schema.json 必须是 JSON 对象'
    return ''
  } catch {
    return 'schema.json JSON 格式无效'
  }
})

async function load() {
  error.value = ''
  try {
    data.value = await api.get(`/tools/${encodeURIComponent(name)}/edit`)
    schemaText.value = JSON.stringify(data.value.tool_schema || {}, null, 2)
    implementation.value = data.value.implementation || ''
    description.value = data.value.capability?.description || ''
    tags.value = (data.value.capability?.tags || []).join(', ')
    markClean()
  } catch (e) {
    error.value = e.message
  }
}

async function save() {
  error.value = ''
  notice.value = ''
  if (schemaError.value) {
    error.value = schemaError.value
    return
  }
  saving.value = true
  try {
    const body = await api.put(`/tools/${encodeURIComponent(name)}/edit`, {
      tool_schema: JSON.parse(schemaText.value || '{}'),
      implementation: implementation.value,
      description: description.value,
      category: data.value?.capability?.category || '',
      tags: tags.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
    })
    data.value = body
    schemaText.value = JSON.stringify(body.tool_schema || {}, null, 2)
    implementation.value = body.implementation || ''
    notice.value = `已保存为新版本草稿 v${body.capability.version}，可提交审核`
    markClean()
  } catch (e) {
    error.value = e.message
  } finally {
    saving.value = false
  }
}

async function submitReview() {
  error.value = ''
  try {
    const cap = await api.post(`/publish/capabilities/${data.value.capability.id}/submit`)
    notice.value = '已提交审核'
    data.value.capability = cap
    await load()
  } catch (e) {
    error.value = e.message
  }
}

function onAi(fields) {
  if (fields.tool_schema) schemaText.value = JSON.stringify(fields.tool_schema, null, 2)
  if (fields.implementation) implementation.value = fields.implementation
  if (fields.description) description.value = fields.description
  if (Array.isArray(fields.tags) && fields.tags.length) tags.value = fields.tags.join(', ')
}

onMounted(load)
</script>

<template>
  <div v-if="error && !data" class="empty">{{ error }}</div>
  <div v-else-if="data" class="editor">
    <div class="flex-between mb-16">
      <div>
        <h2 style="margin: 0">编辑工具：{{ data.capability.name }}</h2>
        <div class="muted" style="font-size: 13px">
          <StatusBadge :status="data.capability.status" />
          当前编辑版本 v{{ data.capability.version }}
          <span v-if="data.base_version"> · 基线版本 v{{ data.base_version }}</span>
          · 保存即生成新版本草稿，其它包内文件自动保留
        </div>
      </div>
      <div class="flex" style="gap: 10px">
        <router-link :to="`/capabilities/${data.capability.id}`" class="btn">查看详情</router-link>
        <button class="btn btn-primary" :disabled="saving || !!schemaError" @click="save">
          {{ saving ? '保存中…' : '保存为新版本' }}
        </button>
      </div>
    </div>

    <div v-if="error" class="alert alert-error mb-16">{{ error }}</div>
    <div v-if="notice" class="alert alert-success mb-16">{{ notice }}</div>

    <AiDraftPanel
      kind="tool"
      :name="name"
      hint="例如：一个把 Excel 明细按月份汇总的工具，入参文件路径与月份，返回汇总 JSON"
      @apply="onAi"
    />

    <div class="grid" style="grid-template-columns: 1fr 320px; align-items: start">
      <div>
        <div class="panel">
          <h3>schema.json（输入/输出契约）</h3>
          <CodeEditor v-model="schemaText" language="json" height="min(48vh, 560px)" />
          <div v-if="schemaError" class="muted" style="color: var(--danger); font-size: 12px; margin-top: 6px">{{ schemaError }}</div>
        </div>

        <div class="panel mt-16">
          <h3>implementation/tool.py</h3>
          <CodeEditor v-model="implementation" language="python" height="min(56vh, 680px)" />
        </div>

        <div class="panel mt-16">
          <h3>描述</h3>
          <textarea v-model="description" class="textarea" rows="3"></textarea>
        </div>

        <div class="panel mt-16">
          <h3>标签（逗号分隔）</h3>
          <input v-model="tags" class="input" placeholder="如：文本处理, 工具" />
        </div>

        <div class="panel mt-16 flex-between">
          <span class="muted" style="font-size: 13px">保存后草稿需要提交审核，管理员通过后才会成为正式版</span>
          <button
            v-if="['draft', 'returned', 'rejected'].includes(data.capability.status)"
            class="btn btn-success"
            @click="submitReview"
          >
            提交审核
          </button>
        </div>
      </div>

      <div class="panel">
        <h3>附属文件（自动保留）</h3>
        <div v-if="data.files.length === 0" class="muted" style="font-size: 13px">无附属文件</div>
        <div v-for="f in data.files" :key="f.path" class="file-item">
          <code>{{ f.path }}</code>
          <span class="muted" style="font-size: 12px">{{ formatSize(f.size) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.editor { max-width: 1520px; }
h3 { margin: 0 0 12px; }
.file-item {
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 13px;
}
.file-item:last-child { border-bottom: none; }
.file-item code { word-break: break-all; }
</style>
