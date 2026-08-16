<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { api } from '../api'
import { TYPE_LABELS } from '../utils/format'

const props = defineProps({
  show: { type: Boolean, default: false },
  cap: { type: Object, default: null },
  title: { type: String, default: '调用 / 调试' }
})
const emit = defineEmits(['close'])

const params = reactive({})
const complexParams = reactive({})
const jsonInput = ref('{}')
const textInput = ref('')
const mode = ref('form')
const result = ref(null)
const error = ref('')
const busy = ref(false)
const copied = ref(false)
const mcpTools = ref([])
const mcpSelected = ref('')
const mcpParams = ref('{}')
const mcpConnecting = ref(false)
const mcpConnected = ref(false)
const mcpError = ref('')

const schema = computed(() => props.cap?.input_schema || {})
const hasSchema = computed(() => Object.keys(schema.value.properties || {}).length > 0)
const isTool = computed(() => props.cap?.type === 'tool')
const isAgent = computed(() => props.cap?.type === 'agent')
const isSkill = computed(() => props.cap?.type === 'skill')
const isWorkflow = computed(() => props.cap?.type === 'workflow')
const isMcp = computed(() => props.cap?.type === 'mcp')
const selectedMcpTool = computed(() => mcpTools.value.find((t) => t.name === mcpSelected.value) || null)

function defaultValue(prop) {
  if (prop.default !== undefined) return prop.default
  if (prop.type === 'boolean') return false
  if (prop.type === 'integer' || prop.type === 'number') return 0
  if (prop.type === 'array') return []
  if (prop.type === 'object') return {}
  if (Array.isArray(prop.enum) && prop.enum.length) return prop.enum[0]
  return ''
}

function schemaDefaults(schema) {
  const out = {}
  for (const [name, prop] of Object.entries(schema?.properties || {})) {
    out[name] = defaultValue(prop)
  }
  return out
}

function isComplex(prop) {
  return prop?.type === 'object' || prop?.type === 'array'
}

function reset() {
  result.value = null
  error.value = ''
  busy.value = false
  copied.value = false
  Object.keys(params).forEach((k) => delete params[k])
  Object.keys(complexParams).forEach((k) => delete complexParams[k])
  jsonInput.value = '{}'
  textInput.value = ''
  mcpTools.value = []
  mcpSelected.value = ''
  mcpParams.value = '{}'
  mcpConnecting.value = false
  mcpConnected.value = false
  mcpError.value = ''
  if (isTool.value && hasSchema.value) {
    mode.value = 'form'
    for (const [name, prop] of Object.entries(schema.value.properties || {})) {
      if (isComplex(prop)) {
        complexParams[name] = JSON.stringify(defaultValue(prop), null, 2)
      } else {
        params[name] = defaultValue(prop)
      }
    }
  } else {
    mode.value = 'json'
  }
}

watch(
  () => props.show,
  (v) => {
    if (v) reset()
  }
)

watch(mcpSelected, (name) => {
  const tool = mcpTools.value.find((t) => t.name === name)
  mcpParams.value = JSON.stringify(schemaDefaults(tool?.inputSchema), null, 2)
})

function isRequired(name) {
  return (schema.value.required || []).includes(name)
}

function collectToolParams() {
  const input = {}
  for (const [name, prop] of Object.entries(schema.value.properties || {})) {
    if (isComplex(prop)) {
      try {
        input[name] = JSON.parse(complexParams[name] || 'null')
      } catch {
        throw new Error(`参数 ${name} 不是合法 JSON`)
      }
    } else {
      input[name] = params[name]
    }
  }
  return input
}

function switchMode(m) {
  mode.value = m
  if (m === 'json') {
    jsonInput.value = JSON.stringify(schemaDefaults(schema.value), null, 2)
  }
}

async function run() {
  error.value = ''
  result.value = null
  const cap = props.cap
  if (!cap) return
  busy.value = true
  try {
    const name = encodeURIComponent(cap.name)
    let path = ''
    let payload = {}
    if (isTool.value) {
      path = `/runtime/tools/${name}/invoke`
      payload = { params: mode.value === 'form' ? collectToolParams() : JSON.parse(jsonInput.value || '{}') }
    } else if (isAgent.value) {
      path = `/runtime/agents/${name}/tasks`
      payload = { task: textInput.value }
    } else if (isSkill.value) {
      path = `/runtime/skills/${name}/activate`
      payload = { context: textInput.value }
    } else if (isWorkflow.value) {
      path = `/runtime/workflows/${name}/executions`
      payload = { input: JSON.parse(jsonInput.value || '{}') }
    } else {
      path = `/runtime/mcp/${name}/install`
      payload = { config: JSON.parse(jsonInput.value || '{}') }
    }
    result.value = await api.post(path, payload)
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function copyResult() {
  try {
    await navigator.clipboard.writeText(JSON.stringify(result.value, null, 2))
    copied.value = true
    setTimeout(() => (copied.value = false), 1500)
  } catch {
    copied.value = false
  }
}

async function connectMcp() {
  const cap = props.cap
  if (!cap) return
  mcpError.value = ''
  mcpConnecting.value = true
  try {
    const res = await api.post(`/runtime/mcp/${encodeURIComponent(cap.name)}/connect`)
    mcpConnected.value = !!res.connected
    mcpTools.value = res.tools || []
    if (!res.connected) mcpError.value = res.error || '连接失败'
  } catch (e) {
    mcpError.value = e.message
  } finally {
    mcpConnecting.value = false
  }
}

async function callMcp() {
  const cap = props.cap
  if (!cap || !mcpSelected.value) return
  result.value = null
  error.value = ''
  busy.value = true
  try {
    result.value = await api.post(`/runtime/mcp/${encodeURIComponent(cap.name)}/call`, {
      tool: mcpSelected.value,
      params: JSON.parse(mcpParams.value || '{}')
    })
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

const resultState = computed(() => {
  const r = result.value
  if (!r) return null
  if (isTool.value) {
    return {
      ok: r.result?.status === 'ok',
      label: r.result?.status === 'ok' ? '调用成功' : '调用失败',
      extra: r.result?.execution ? `执行方式：${r.result.execution}` : ''
    }
  }
  if (isAgent.value) {
    return {
      ok: r.mode === 'llm',
      label: r.mode === 'llm' ? '真实执行（LLM）' : '模拟执行',
      extra: `工具调用 ${r.tool_calls ?? 0} 次`
    }
  }
  if (isWorkflow.value) {
    return {
      ok: r.state === 'succeeded',
      label: `状态：${r.state}`,
      extra: r.error || ''
    }
  }
  if (isSkill.value) return { ok: !!r.activated, label: r.activated ? '已激活' : '激活失败', extra: '' }
  if (isMcp.value && r.tool) return { ok: true, label: `MCP 工具：${r.tool}`, extra: '' }
  return { ok: !!r.installed, label: r.installed ? '已安装' : '安装失败', extra: '' }
})

function stepBadge(name) {
  if (name === 'skill') return { label: '技能', cls: 'badge-primary' }
  if (String(name).startsWith('mcp_')) return { label: 'MCP', cls: 'badge-warning' }
  return { label: '工具', cls: 'badge' }
}
</script>

<template>
  <div v-if="show && cap" class="modal-mask" @click.self="emit('close')">
    <div class="modal panel">
      <div class="modal-header">
        <div>
          <h3 style="margin: 0">{{ title }}：{{ cap.name }}</h3>
          <div class="muted" style="font-size: 12px; margin-top: 4px">
            {{ TYPE_LABELS[cap.type] }} · v{{ cap.version }}
            <span v-if="cap.description" style="margin-left: 8px">{{ cap.description }}</span>
          </div>
        </div>
        <button class="modal-close" @click="emit('close')">✕</button>
      </div>

      <div v-if="error" class="alert alert-error">{{ error }}</div>

      <!-- 工具：Schema 表单 / JSON 两种模式 -->
      <template v-if="isTool">
        <div v-if="hasSchema" class="mode-bar">
          <button class="mode-btn" :class="{ active: mode === 'form' }" @click="mode = 'form'">参数表单</button>
          <button class="mode-btn" :class="{ active: mode === 'json' }" @click="switchMode('json')">JSON</button>
        </div>

        <div v-if="mode === 'form' && hasSchema" class="form-grid">
          <div v-for="(prop, name) in schema.properties" :key="name" class="field">
            <label>
              <code>{{ name }}</code>
              <span v-if="isRequired(name)" class="req">*</span>
              <span v-if="prop.description || prop.title" class="muted">（{{ prop.description || prop.title }}）</span>
            </label>
            <select v-if="Array.isArray(prop.enum)" v-model="params[name]" class="select">
              <option v-for="opt in prop.enum" :key="opt" :value="opt">{{ opt }}</option>
            </select>
            <label v-else-if="prop.type === 'boolean'" class="check-line">
              <input type="checkbox" v-model="params[name]" />
              <span>{{ params[name] ? '是' : '否' }}</span>
            </label>
            <input
              v-else-if="prop.type === 'number' || prop.type === 'integer'"
              v-model.number="params[name]"
              type="number"
              class="input"
            />
            <textarea
              v-else-if="isComplex(prop)"
              v-model="complexParams[name]"
              class="textarea"
              rows="3"
              spellcheck="false"
            ></textarea>
            <input v-else v-model="params[name]" class="input" :placeholder="prop.description || name" />
          </div>
        </div>

        <div v-else class="field">
          <label>参数 JSON</label>
          <textarea v-model="jsonInput" class="textarea" rows="8" spellcheck="false"></textarea>
        </div>
      </template>

      <!-- Agent / 技能 -->
      <template v-else-if="isAgent || isSkill">
        <div class="field">
          <label>{{ isAgent ? '任务内容' : '任务上下文' }}</label>
          <textarea v-model="textInput" class="textarea" rows="6" :placeholder="isAgent ? '如：生成上月销售报表' : '如：写测试'"></textarea>
        </div>
      </template>

      <!-- MCP：连接 → 发现工具 → 选工具调用 -->
      <template v-else-if="isMcp">
        <div class="flex mt-8" style="gap: 10px; align-items: center">
          <button class="btn btn-primary" :disabled="mcpConnecting" @click="connectMcp">
            {{ mcpConnecting ? '连接中…' : mcpConnected ? '重新连接并发现工具' : '连接并发现工具' }}
          </button>
          <span v-if="mcpConnected" class="badge badge-success">已连接</span>
        </div>
        <div v-if="mcpError" class="alert alert-error mt-12">{{ mcpError }}</div>

        <template v-if="mcpTools.length">
          <div class="field mt-12">
            <label>选择 MCP 工具</label>
            <select v-model="mcpSelected" class="select">
              <option value="">选择工具…</option>
              <option v-for="t in mcpTools" :key="t.name" :value="t.name">{{ t.name }}</option>
            </select>
          </div>
          <div v-if="selectedMcpTool?.description" class="muted mt-8" style="font-size: 12px">
            {{ selectedMcpTool.description }}
          </div>
          <div v-if="mcpSelected" class="field mt-12">
            <label>参数 JSON</label>
            <textarea v-model="mcpParams" class="textarea" rows="6" spellcheck="false"></textarea>
          </div>
          <button v-if="mcpSelected" class="btn btn-primary mt-12" :disabled="busy" @click="callMcp">
            {{ busy ? '调用中…' : '调用工具' }}
          </button>
        </template>
        <div v-else-if="mcpConnected" class="muted mt-12" style="font-size: 13px">该 MCP 没有暴露工具</div>
      </template>

      <!-- 工作流：JSON 入参 -->
      <template v-else>
        <div class="field">
          <label>入参 JSON</label>
          <textarea v-model="jsonInput" class="textarea" rows="8" spellcheck="false"></textarea>
        </div>
      </template>

      <div class="modal-foot">
        <span class="muted" style="font-size: 12px">调用会计入用量；需为管理员、能力作者或已加入者</span>
        <div class="flex" style="gap: 10px">
          <button class="btn" @click="emit('close')">关闭</button>
          <button v-if="!isMcp" class="btn btn-primary" :disabled="busy" @click="run">{{ busy ? '执行中…' : '执行' }}</button>
        </div>
      </div>

      <div v-if="result" class="result mt-16">
        <div class="flex-between">
          <h4 style="margin: 0">执行结果</h4>
          <div class="flex" style="gap: 10px">
            <span class="badge" :class="resultState.ok ? 'badge-success' : 'badge-danger'">{{ resultState.label }}</span>
            <span v-if="resultState.extra" class="muted" style="font-size: 12px">{{ resultState.extra }}</span>
            <button class="btn btn-sm" @click="copyResult">{{ copied ? '已复制' : '复制' }}</button>
          </div>
        </div>
        <div v-if="isAgent && result.steps && result.steps.length" class="steps mt-16">
          <h4 style="margin: 0 0 10px">执行过程（{{ result.steps.length }} 步）</h4>
          <div v-for="(s, i) in result.steps" :key="i" class="step">
            <template v-if="s.kind === 'call'">
              <div class="step-head">
                <span class="step-no">{{ i + 1 }}</span>
                <span class="badge" :class="stepBadge(s.name).cls">{{ stepBadge(s.name).label }}</span>
                <code>{{ s.name }}</code>
              </div>
              <pre v-if="s.arguments && s.arguments !== '{}'" class="step-args">{{ s.arguments }}</pre>
            </template>
            <template v-else-if="s.kind === 'result'">
              <div class="step-head"><span class="step-no dim">↳</span><span class="muted">返回</span></div>
              <pre class="step-result">{{ s.result }}</pre>
            </template>
            <template v-else>
              <div class="step-head"><span class="step-no">{{ i + 1 }}</span><span class="badge badge-success">最终输出</span></div>
              <pre class="step-result">{{ s.output }}</pre>
            </template>
          </div>
        </div>
        <pre class="json-pre mt-8">{{ JSON.stringify(result, null, 2) }}</pre>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-mask {
  position: fixed; inset: 0; background: rgba(5, 8, 16, 0.72); z-index: 100;
  display: flex; align-items: center; justify-content: center; padding: 20px;
}
.modal {
  width: 720px; max-width: 100%; max-height: 90vh; overflow: auto;
  box-shadow: 0 24px 64px rgba(0,0,0,.55);
}
.modal-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 16px; }
.modal-close { background: none; border: none; color: var(--muted); font-size: 16px; cursor: pointer; }
.modal-close:hover { color: var(--text); }
.mode-bar { display: flex; gap: 4px; margin-bottom: 14px; }
.mode-btn {
  background: none; border: 1px solid var(--border); color: var(--muted);
  padding: 5px 14px; border-radius: 8px; cursor: pointer; font-size: 13px;
}
.mode-btn.active { color: var(--primary); border-color: var(--primary); background: rgba(79,140,255,.1); }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.field { display: flex; flex-direction: column; gap: 6px; }
.field label { font-size: 13px; color: var(--muted); }
.field label code { color: var(--text); }
.req { color: var(--danger); margin-left: 2px; }
.check-line { display: flex; align-items: center; gap: 8px; cursor: pointer; }
.modal-foot { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-top: 18px; }
.result { border-top: 1px solid var(--border); padding-top: 16px; }
.json-pre {
  background: var(--panel-2); border: 1px solid var(--border); border-radius: 8px;
  padding: 12px; font-size: 12px; overflow: auto; max-height: 320px; white-space: pre-wrap;
}
.steps { border-top: 1px solid var(--border); padding-top: 14px; }
.step { margin-bottom: 10px; }
.step-head { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.step-no {
  width: 20px; height: 20px; border-radius: 50%; background: rgba(79,140,255,.15);
  color: var(--primary); display: inline-flex; align-items: center; justify-content: center;
  font-size: 11px; flex: none;
}
.step-no.dim { background: var(--panel-2); color: var(--muted); }
.step-args, .step-result {
  margin: 6px 0 0 28px; background: var(--panel-2); border: 1px solid var(--border);
  border-radius: 8px; padding: 8px 10px; font-size: 12px; white-space: pre-wrap;
  max-height: 180px; overflow: auto;
}
@media (max-width: 700px) {
  .form-grid { grid-template-columns: 1fr; }
}
</style>
