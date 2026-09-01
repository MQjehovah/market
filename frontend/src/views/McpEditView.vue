<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import { formatSize } from '../utils/format'
import StatusBadge from '../components/StatusBadge.vue'

const route = useRoute()
const name = route.params.name

const data = ref(null)
const transport = ref('stdio')
const command = ref('')
const argsText = ref('')
const url = ref('')
const server = ref('')
const envText = ref('{}')
const headersText = ref('{}')
const toolsText = ref('')
const implementations = ref([])
const activeImpl = ref(0)
const newImplName = ref('')
const description = ref('')
const tags = ref('')
const error = ref('')
const notice = ref('')
const saving = ref(false)

const activeFile = computed(() => implementations.value[activeImpl.value] || null)

const jsonFieldError = computed(() => {
  for (const [label, text] of [
    ['env', envText.value],
    ['headers', headersText.value]
  ]) {
    try {
      const v = JSON.parse(text || '{}')
      if (v === null || typeof v !== 'object' || Array.isArray(v)) {
        return `${label} 必须是 JSON 对象`
      }
    } catch {
      return `${label} JSON 格式无效`
    }
  }
  if (toolsText.value.trim()) {
    try {
      JSON.parse(toolsText.value)
    } catch {
      return 'tools.json JSON 格式无效'
    }
  }
  return ''
})

function applyConnection(conn = {}) {
  transport.value = conn.transport || conn.type || 'stdio'
  command.value = conn.command || ''
  argsText.value = Array.isArray(conn.args) ? conn.args.join(' ') : ''
  url.value = conn.url || ''
  server.value = conn.server || ''
  envText.value = JSON.stringify(conn.env && typeof conn.env === 'object' ? conn.env : {}, null, 2)
  headersText.value = JSON.stringify(
    conn.headers && typeof conn.headers === 'object' ? conn.headers : {},
    null,
    2
  )
}

function buildConnection() {
  const conn = {
    transport: transport.value || 'stdio'
  }
  if (command.value.trim()) conn.command = command.value.trim()
  if (argsText.value.trim()) {
    conn.args = argsText.value.trim().split(/\s+/).filter(Boolean)
  }
  if (url.value.trim()) conn.url = url.value.trim()
  if (server.value.trim()) conn.server = server.value.trim()
  const env = JSON.parse(envText.value || '{}')
  const headers = JSON.parse(headersText.value || '{}')
  if (Object.keys(env).length) conn.env = env
  if (Object.keys(headers).length) conn.headers = headers
  return conn
}

function applyImplementations(list) {
  implementations.value = (list || []).map((f) => ({
    path: f.path,
    content: f.content || ''
  }))
  if (!implementations.value.length) {
    implementations.value = [
      {
        path: 'implementation/server.py',
        content:
          '"""MCP stdio server 入口。网关会把 implementation/*.py 解到临时目录后按 connection.args 启动。"""\n\n\ndef main() -> None:\n    raise SystemExit("请实现 MCP server")\n\n\nif __name__ == "__main__":\n    main()\n'
      }
    ]
  }
  activeImpl.value = 0
}

function selectImpl(i) {
  activeImpl.value = i
}

function addImpl() {
  error.value = ''
  let namePart = (newImplName.value || '').trim().replace(/\\/g, '/')
  if (!namePart) namePart = 'server.py'
  if (!namePart.endsWith('.py')) namePart += '.py'
  namePart = namePart.replace(/^implementation\//, '')
  const path = `implementation/${namePart}`
  if (!/^implementation\/[\w.\-]+(?:\/[\w.\-]+)*\.py$/.test(path)) {
    error.value = '文件名只能包含字母、数字、下划线、短横线与点'
    return
  }
  if (implementations.value.some((f) => f.path === path)) {
    error.value = `已存在 ${path}`
    return
  }
  implementations.value.push({ path, content: '' })
  activeImpl.value = implementations.value.length - 1
  newImplName.value = ''
}

function removeImpl(i) {
  if (implementations.value.length <= 1) {
    error.value = '至少保留一个 implementation/*.py'
    return
  }
  implementations.value.splice(i, 1)
  if (activeImpl.value >= implementations.value.length) {
    activeImpl.value = implementations.value.length - 1
  }
}

async function load() {
  error.value = ''
  try {
    data.value = await api.get(`/mcp/${encodeURIComponent(name)}/edit`)
    applyConnection(data.value.connection || {})
    toolsText.value =
      data.value.tools_json == null ? '' : JSON.stringify(data.value.tools_json, null, 2)
    applyImplementations(data.value.implementations)
    description.value = data.value.capability?.description || ''
    tags.value = (data.value.capability?.tags || []).join(', ')
  } catch (e) {
    error.value = e.message
  }
}

async function save() {
  error.value = ''
  notice.value = ''
  if (jsonFieldError.value) {
    error.value = jsonFieldError.value
    return
  }
  saving.value = true
  try {
    const payload = {
      connection: buildConnection(),
      implementations: implementations.value.map((f) => ({
        path: f.path,
        content: f.content
      })),
      description: description.value,
      category: data.value?.capability?.category || '',
      tags: tags.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
    }
    if (toolsText.value.trim()) {
      payload.tools_json = JSON.parse(toolsText.value)
    }
    const body = await api.put(`/mcp/${encodeURIComponent(name)}/edit`, payload)
    data.value = body
    applyConnection(body.connection || {})
    toolsText.value = body.tools_json == null ? '' : JSON.stringify(body.tools_json, null, 2)
    applyImplementations(body.implementations)
    notice.value = `已保存为新版本草稿 v${body.capability.version}，可提交审核`
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

onMounted(load)
</script>

<template>
  <div v-if="error && !data" class="empty">{{ error }}</div>
  <div v-else-if="data" class="editor">
    <div class="flex-between mb-16">
      <div>
        <h2 style="margin: 0">编辑 MCP：{{ data.capability.name }}</h2>
        <div class="muted" style="font-size: 13px">
          <StatusBadge :status="data.capability.status" />
          当前编辑版本 v{{ data.capability.version }}
          <span v-if="data.base_version"> · 基线版本 v{{ data.base_version }}</span>
          · 保存即生成新版本草稿，security 等附属文件自动保留
        </div>
      </div>
      <div class="flex" style="gap: 10px">
        <router-link :to="`/capabilities/${data.capability.id}`" class="btn">查看详情</router-link>
        <button class="btn btn-primary" :disabled="saving || !!jsonFieldError" @click="save">
          {{ saving ? '保存中…' : '保存为新版本' }}
        </button>
      </div>
    </div>

    <div v-if="error" class="alert alert-error mb-16">{{ error }}</div>
    <div v-if="notice" class="alert alert-success mb-16">{{ notice }}</div>

    <div class="grid" style="grid-template-columns: 1fr 320px; align-items: start">
      <div>
        <div class="panel">
          <h3>连接配置（connection.json）</h3>
          <div class="field-grid">
            <label>
              <span>Transport</span>
              <select v-model="transport" class="input">
                <option value="stdio">stdio</option>
                <option value="http">http</option>
                <option value="sse">sse</option>
                <option value="streamable_http">streamable_http</option>
              </select>
            </label>
            <label>
              <span>Command（stdio）</span>
              <input v-model="command" class="input" placeholder="如：python" />
            </label>
            <label class="full">
              <span>Args（空格分隔）</span>
              <input
                v-model="argsText"
                class="input"
                placeholder="如：server.py 或 -m your_mcp_server"
              />
            </label>
            <label class="full">
              <span>URL（http / sse）</span>
              <input v-model="url" class="input" placeholder="https://..." />
            </label>
            <label class="full">
              <span>Gateway Server 名（可选）</span>
              <input v-model="server" class="input" placeholder="已注册的网关服务名" />
            </label>
            <label class="full">
              <span>Env（JSON 对象）</span>
              <textarea v-model="envText" class="textarea code-editor" rows="5" spellcheck="false"></textarea>
            </label>
            <label class="full">
              <span>Headers（JSON 对象）</span>
              <textarea v-model="headersText" class="textarea code-editor" rows="4" spellcheck="false"></textarea>
            </label>
          </div>
          <div v-if="jsonFieldError" class="muted" style="color: var(--danger); font-size: 12px; margin-top: 6px">
            {{ jsonFieldError }}
          </div>
        </div>

        <div class="panel mt-16">
          <div class="flex-between flex-wrap" style="margin-bottom: 12px">
            <h3 style="margin: 0">implementation/*.py</h3>
            <span class="muted" style="font-size: 12px">stdio 时网关会解压这些文件后按 Args 启动</span>
          </div>
          <div class="impl-tabs">
            <button
              v-for="(f, i) in implementations"
              :key="f.path"
              type="button"
              class="impl-tab"
              :class="{ active: i === activeImpl }"
              @click="selectImpl(i)"
            >
              {{ f.path.replace(/^implementation\//, '') }}
              <span
                v-if="implementations.length > 1"
                class="impl-remove"
                title="移除"
                @click.stop="removeImpl(i)"
              >×</span>
            </button>
          </div>
          <div class="impl-add flex" style="gap: 8px; margin: 10px 0 12px">
            <input
              v-model="newImplName"
              class="input"
              style="flex: 1"
              placeholder="新增文件名，如 helpers.py"
              @keyup.enter="addImpl"
            />
            <button class="btn btn-sm" type="button" @click="addImpl">添加</button>
          </div>
          <textarea
            v-if="activeFile"
            v-model="activeFile.content"
            class="textarea code-editor"
            rows="16"
            spellcheck="false"
          ></textarea>
        </div>

        <div class="panel mt-16">
          <h3>tools.json（可选，留空则保留原文件）</h3>
          <textarea
            v-model="toolsText"
            class="textarea code-editor"
            rows="8"
            spellcheck="false"
            placeholder="留空表示不覆盖包内 tools.json"
          ></textarea>
        </div>

        <div class="panel mt-16">
          <h3>描述</h3>
          <textarea v-model="description" class="textarea" rows="3"></textarea>
        </div>

        <div class="panel mt-16">
          <h3>标签（逗号分隔）</h3>
          <input v-model="tags" class="input" placeholder="如：文件系统, MCP" />
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
.editor { max-width: 1080px; }
h3 { margin: 0 0 12px; }
.code-editor {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 13px; line-height: 1.55;
}
.field-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.field-grid label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
}
.field-grid label.full { grid-column: 1 / -1; }
.impl-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.impl-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-radius: 8px;
  border: 1px solid var(--border-strong);
  background: var(--panel-2);
  color: var(--muted);
  cursor: pointer;
  font-size: 12px;
}
.impl-tab.active {
  border-color: var(--primary);
  color: var(--primary);
  background: var(--primary-soft);
}
.impl-remove {
  font-size: 14px;
  line-height: 1;
  opacity: 0.7;
}
.impl-remove:hover { opacity: 1; color: var(--danger); }
.file-item {
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 13px;
}
.file-item:last-child { border-bottom: none; }
.file-item code { word-break: break-all; }
@media (max-width: 800px) {
  .field-grid { grid-template-columns: 1fr; }
}
</style>
