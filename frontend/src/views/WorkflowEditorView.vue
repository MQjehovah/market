<script setup>
import { computed, onMounted, provide, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { VueFlow, useVueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { api } from '../api'
import { authState } from '../stores/auth'
import { TYPE_CATEGORIES, TYPE_LABELS, formatDate, isOpenDraft } from '../utils/format'
import StatusBadge from '../components/StatusBadge.vue'
import WorkflowNode from '../components/workflow/WorkflowNode.vue'
import VarInput from '../components/workflow/VarInput.vue'
import HtmlPreview from '../components/workflow/HtmlPreview.vue'
import CodeEditor from '../components/CodeEditor.vue'
import { pickHtmlOutput } from '../utils/htmlOutput'
import { bindUnsavedGuard } from '../utils/unsaved'

const props = defineProps({ id: { type: String, default: '' } })
const route = useRoute()
const router = useRouter()
const { screenToFlowCoordinate } = useVueFlow()

const ALL_TYPES = [
  'start', 'end', 'answer', 'llm', 'agent', 'parameter-extractor', 'knowledge-retrieval',
  'if-else', 'question-classifier', 'iteration', 'loop', 'variable-aggregator', 'variable-assigner',
  'code', 'http-request', 'template-transform', 'doc-extractor', 'list-operator', 'approval',
  'tool', 'skill', 'mcp', 'workflow'
]
const nodeTypes = Object.fromEntries(ALL_TYPES.map((t) => [t, WorkflowNode]))

const PALETTE = [
  { group: '基础', type: 'start', label: '开始', desc: '工作流入口', color: '#2fbf71', icon: 'IN' },
  { group: '基础', type: 'end', label: '结束', desc: '汇总输出', color: '#e5534b', icon: 'OUT' },
  { group: '基础', type: 'answer', label: '回复', desc: '直接回复(chatflow)', color: '#2fbf71', icon: 'ANS' },
  { group: 'LLM', type: 'llm', label: 'LLM', desc: '调用大模型', color: '#4f8cff', icon: 'LLM' },
  { group: 'LLM', type: 'agent', label: 'Agent', desc: '委派 A2A 专家', color: '#9d6bff', icon: 'AGT' },
  { group: 'LLM', type: 'parameter-extractor', label: '参数提取', desc: '结构化抽取', color: '#9d6bff', icon: 'PAR' },
  { group: 'LLM', type: 'knowledge-retrieval', label: '知识检索', desc: '检索知识库', color: '#2fbf71', icon: 'KB' },
  { group: '逻辑', type: 'if-else', label: '条件分支', desc: 'true/false 分流', color: '#e2a93b', icon: 'IF' },
  { group: '逻辑', type: 'question-classifier', label: '问题分类', desc: 'LLM 分类分流', color: '#e2a93b', icon: 'CLS' },
  { group: '逻辑', type: 'iteration', label: '迭代', desc: '对数组逐项执行', color: '#e2a93b', icon: 'IT' },
  { group: '逻辑', type: 'loop', label: '循环', desc: '条件循环', color: '#e2a93b', icon: 'LP' },
  { group: '逻辑', type: 'variable-aggregator', label: '变量聚合', desc: '多路汇聚', color: '#e2a93b', icon: 'AGG' },
  { group: '逻辑', type: 'variable-assigner', label: '变量赋值', desc: '写会话变量', color: '#e2a93b', icon: 'ASN' },
  { group: '逻辑', type: 'approval', label: '人工审批', desc: '暂停等待审批', color: '#e2a93b', icon: 'APR' },
  { group: '数据', type: 'code', label: '代码', desc: '沙箱执行', color: '#4f8cff', icon: '</>' },
  { group: '数据', type: 'http-request', label: 'HTTP', desc: 'HTTP 请求', color: '#4f8cff', icon: 'HTTP' },
  { group: '数据', type: 'template-transform', label: '模板', desc: '文本/JSON 变换', color: '#4f8cff', icon: 'TPL' },
  { group: '数据', type: 'doc-extractor', label: '文档抽取', desc: '提取文本/JSON', color: '#4f8cff', icon: 'DOC' },
  { group: '数据', type: 'list-operator', label: '列表操作', desc: '过滤/排序/取首', color: '#4f8cff', icon: 'LST' },
  { group: '市场能力', type: 'tool', label: '工具', desc: '调用市场工具', color: '#4f8cff', icon: 'TL' },
  { group: '市场能力', type: 'skill', label: '技能', desc: '激活执行技能', color: '#2fbf71', icon: 'SK' },
  { group: '市场能力', type: 'mcp', label: '连接器', desc: '调用/安装 MCP', color: '#e2a93b', icon: 'MCP' },
  { group: '市场能力', type: 'workflow', label: '工作流', desc: '调用其它工作流', color: '#0ea5e9', icon: 'WF' }
]
const PALETTE_GROUPS = ['基础', 'LLM', '逻辑', '数据', '市场能力']

const meta = reactive({
  id: '',
  name: '',
  description: '',
  version: '0.1.0',
  category: '工作流',
  tags: '',
  visibility: 'internal',
  status: 'draft',
  author_id: ''
})
const wfSettings = reactive({ on_error: 'fail', timeout_seconds: 0 })
const presentation = reactive({ mode: 'auto' })
const PRESENTATION_OPTIONS = [
  { value: 'auto', label: '自动（按形态推断）' },
  { value: 'form', label: '表单' },
  { value: 'chat', label: '对话' },
  { value: 'automation', label: '自动化' }
]
function setPresentation(p) {
  presentation.mode = ['auto', 'form', 'chat', 'automation'].includes(p?.mode) ? p.mode : 'auto'
}
const trigger = reactive({
  type: 'none',
  token: '',
  cron: '0 9 * * *',
  enabled: true,
  inputText: '{}'
})
const TRIGGER_LABELS = {
  none: '手动 / 被调用',
  webhook: 'Webhook',
  gitlab: 'GitLab Webhook',
  schedule: '定时（cron）'
}
const CRON_PRESETS = [
  { label: '每天 09:00', value: '0 9 * * *' },
  { label: '每天 08:30', value: '30 8 * * *' },
  { label: '每周一 09:00', value: '0 9 * * 1' },
  { label: '每周五 17:00', value: '0 17 * * 5' },
  { label: '每月 1 号 09:00', value: '0 9 1 * *' },
  { label: '每 5 分钟', value: '*/5 * * * *' }
]

function setTrigger(t) {
  const type = t?.type || 'none'
  trigger.type = TRIGGER_LABELS[type] ? type : 'none'
  trigger.token = t?.token || ''
  trigger.cron = t?.cron || '0 9 * * *'
  trigger.enabled = t?.enabled !== false
  trigger.inputText = JSON.stringify(t?.input || {}, null, 2)
}

function triggerToJson() {
  if (trigger.type === 'webhook') {
    return { trigger: { type: 'webhook', token: trigger.token.trim() } }
  }
  if (trigger.type === 'gitlab') {
    return { trigger: { type: 'gitlab', token: trigger.token.trim() } }
  }
  if (trigger.type === 'schedule') {
    let input = {}
    try {
      input = JSON.parse(trigger.inputText || '{}')
    } catch {
      input = {}
    }
    return {
      trigger: { type: 'schedule', cron: trigger.cron.trim(), enabled: trigger.enabled, input }
    }
  }
  return {}
}

function genToken() {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

const triggerUrl = computed(() => {
  if (!meta.name || trigger.type === 'none' || trigger.type === 'schedule') return ''
  const origin = typeof window !== 'undefined' ? window.location.origin : ''
  return `${origin}/api/runtime/workflows/${encodeURIComponent(meta.name.trim())}/trigger`
})
const flowNodes = ref([])
const flowEdges = ref([])
const selectedNodeId = ref('')
const capCache = reactive({})
const capLoading = reactive({})
const capLoadError = reactive({})
const paramsText = ref('{}')
const paramsError = ref('')

const lockVersion = ref(true)

const jsonOpen = ref(false)
const jsonText = ref('')
const testInput = ref('{\n  "query": ""\n}')
const execution = ref(null)
const templates = ref([])
const runOpen = ref(false)
const runForm = reactive({})

const error = ref('')
const notice = ref('')
const busy = ref(false)
const submitting = ref(false)
const running = ref(false)

const isOwner = computed(
  () =>
    meta.author_id &&
    authState.user &&
    (authState.user.id === meta.author_id || authState.user.role === 'admin')
)
const canEdit = computed(
  () =>
    !meta.id ||
    (isOwner.value && ['draft', 'returned', 'rejected'].includes(meta.status))
)
const canTest = computed(() => isOwner.value)
const outputNodeId = ref('')

provide('wf-run-state', execution)
provide('wf-can-edit', canEdit)
provide('wf-node-actions', {
  viewOutput: (id) => {
    outputNodeId.value = id
  },
  remove: (id) => deleteNodeById(id)
})
const selectedNode = computed(() =>
  flowNodes.value.find((n) => n.id === selectedNodeId.value)
)
const startFields = computed(() => {
  const start = flowNodes.value.find((n) => (n.data?.node?.type || n.type) === 'start')
  const fields = start?.data?.node?.params?.fields
  return Array.isArray(fields) ? fields.filter(Boolean) : []
})
const startFieldItems = computed(() =>
  startFields.value
    .map((f) =>
      typeof f === 'string'
        ? { key: f, label: f, placeholder: f, default: '' }
        : {
            key: f?.key || '',
            label: f?.label || f?.key || '',
            placeholder: f?.placeholder || f?.label || f?.key || '',
            default: f?.default ?? ''
          }
    )
    .filter((f) => f.key)
)
const selectedCaps = computed(() => capCache[selectedNode.value?.type] || [])
const MARKET_NODE_TYPES = ['tool', 'agent', 'skill', 'mcp', 'workflow']
const isMarketNode = computed(() => MARKET_NODE_TYPES.includes(selectedNode.value?.type))
const selectedTypeLabel = computed(
  () =>
    PALETTE.find((x) => x.type === selectedNode.value?.type)?.label ||
    TYPE_LABELS[selectedNode.value?.type] ||
    selectedNode.value?.type ||
    ''
)
const OUTPUT_FIELDS = {
  llm: ['text'],
  answer: ['answer'],
  agent: ['text'],
  skill: ['skill_md', 'context'],
  mcp: ['result'],
  code: ['result'],
  'if-else': ['result'],
  'question-classifier': ['class_id', 'class_name'],
  'parameter-extractor': ['params'],
  'knowledge-retrieval': ['hits'],
  'list-operator': ['result', 'count'],
  'variable-aggregator': ['result'],
  'variable-assigner': ['assigned'],
  iteration: ['items', 'count'],
  loop: ['results', 'count'],
  'doc-extractor': ['text', 'json'],
  approval: ['approved', 'comment', 'approver'],
  'template-transform': ['text', 'value']
}
// 供 VarInput 使用的可插入变量（系统 / 入参 / 上游节点输出）
const flatVars = computed(() => {
  const out = [
    { group: '系统', label: '用户输入', value: '${sys.query}' },
    { group: '系统', label: '对话历史', value: '${sys.history}' },
    { group: '系统', label: '当前时间', value: '${sys.now}' }
  ]
  try {
    const obj = JSON.parse(testInput.value || '{}')
    Object.keys(obj).forEach((k) =>
      out.push({ group: '入参 input', label: k, value: `\${input.${k}}` })
    )
  } catch {
    // 忽略非法 JSON
  }
  for (const n of flowNodes.value) {
    if (selectedNode.value && n.id === selectedNode.value.id) continue
    const t = n.data?.node?.type || n.type
    out.push({ group: `上游 · ${n.id}`, label: n.id, value: `\${${n.id}}` })
    if (t === 'start') {
      const fs = n.data?.node?.params?.fields || []
      fs.forEach((f) => {
        const key = typeof f === 'string' ? f : f?.key
        if (key) out.push({ group: `上游 · ${n.id}`, label: `input.${key}`, value: `\${input.${key}}` })
      })
    }
    for (const f of OUTPUT_FIELDS[t] || []) {
      out.push({ group: `上游 · ${n.id}`, label: `${n.id}.${f}`, value: `\${${n.id}.${f}}` })
    }
  }
  return out
})

watch(
  () => selectedNodeId.value,
  () => {
    if (selectedNode.value) {
      ensureNodeParams(selectedNode.value.data.node)
      paramsText.value = JSON.stringify(selectedNode.value.data.node.params || {}, null, 2)
      paramsError.value = ''
      if (isMarketNode.value) ensureCaps(selectedNode.value.type)
    }
  }
)

// ---- 节点参数表单（按类型） ----
const SEL_OPS = ['eq', 'ne', 'contains', 'not_empty', 'empty', 'regex', 'gt', 'lt', 'ge', 'le']
const LIST_OPS = ['head', 'tail', 'length', 'unique', 'filter', 'sort']
const FORMS = {
  start: [
    {
      key: 'fields',
      label: '输入字段',
      kind: 'rows',
      grid: true,
      rowFields: [
        { key: 'key', ph: '字段名，如 alert_id', noVars: true },
        { key: 'label', ph: '显示名', noVars: true },
        { key: 'type', kind: 'select', options: ['text', 'number', 'date', 'select', 'textarea'] },
        { key: 'required', kind: 'bool' },
        { key: 'default', ph: '默认值' }
      ],
      newRow: () => ({ key: '', label: '', type: 'text', required: false, default: '' })
    }
  ],
  end: [{ key: 'outputs', label: '输出映射', kind: 'map', phKey: '输出名', phVal: '变量，如 ${input.x}' }],
  answer: [{ key: 'answer', label: '回复内容', kind: 'textarea', ph: '支持 ${节点.字段} 变量' }],
  llm: [
    { key: 'system', label: 'System', kind: 'textarea', ph: '角色设定' },
    { key: 'prompt', label: 'Prompt', kind: 'textarea', ph: '提示词，支持变量' }
  ],
  agent: [
    { key: 'task', label: '任务', kind: 'textarea', ph: '交给该专家处理的任务描述' },
    { key: 'skills', label: '额外技能（可选）', kind: 'strlist', ph: '技能名，如 weekly-report' },
    { key: 'tools', label: '额外工具（可选）', kind: 'strlist', ph: '工具名，如 web_search' },
    { key: 'mcps', label: '额外连接器（可选）', kind: 'strlist', ph: '连接器名，如 erp' }
  ],
  skill: [{ key: 'context', label: '上下文', kind: 'textarea', ph: '传给技能的上下文' }],
  tool: [],
  mcp: [
    { key: 'op', label: '操作', kind: 'select', options: ['call', 'install'] },
    { key: 'tool', label: '工具名（op=call）', kind: 'text', ph: '如 dingtalk_send_message' },
    { key: 'args', label: '参数', kind: 'map', phKey: '参数名', phVal: '值，支持变量' }
  ],
  workflow: [
    { key: 'input', label: '输入参数（子工作流入参）', kind: 'map', phKey: '字段名', phVal: '值/变量' }
  ],
  'knowledge-retrieval': [
    { key: 'query', label: '检索词', kind: 'textarea', ph: '支持变量' },
    { key: 'top_k', label: 'Top K', kind: 'number' }
  ],
  'if-else': [
    { key: 'logic', label: '条件关系', kind: 'select', options: ['and', 'or'] },
    {
      key: 'conditions', label: '条件', kind: 'rows',
      rowFields: [
        { key: 'left', ph: '左值/变量' },
        { key: 'operator', kind: 'select', options: SEL_OPS },
        { key: 'right', ph: '右值' }
      ],
      newRow: () => ({ left: '', operator: 'eq', right: '' })
    }
  ],
  'question-classifier': [
    { key: 'query', label: '待分类文本', kind: 'textarea', ph: '支持变量' },
    {
      key: 'classes', label: '类别', kind: 'rows',
      rowFields: [
        { key: 'id', ph: 'id' },
        { key: 'name', ph: '名称' },
        { key: 'description', ph: '描述' }
      ],
      newRow: () => ({ id: '', name: '', description: '' })
    }
  ],
  'parameter-extractor': [
    { key: 'query', label: '来源文本', kind: 'textarea', ph: '支持变量' },
    {
      key: 'parameters', label: '参数', kind: 'rows',
      rowFields: [
        { key: 'name', ph: '参数名' },
        { key: 'type', ph: '类型', def: 'string' },
        { key: 'description', ph: '说明' }
      ],
      newRow: () => ({ name: '', type: 'string', description: '' })
    }
  ],
  iteration: [{ key: 'items', label: '数组（变量）', kind: 'text', ph: '${节点.列表}' }],
  loop: [{ key: 'max_iterations', label: '最大次数', kind: 'number' }],
  'variable-aggregator': [
    { key: 'mode', label: '聚合方式', kind: 'select', options: ['first', 'append', 'concat'] },
    { key: 'variables', label: '变量', kind: 'strlist', ph: '${节点.字段}' }
  ],
  'variable-assigner': [{ key: 'assignments', label: '赋值', kind: 'map', phKey: '变量名', phVal: '值/变量' }],
  code: [
    { key: 'language', label: '语言', kind: 'select', options: ['python'] },
    { key: 'code', label: '代码', kind: 'code' },
    { key: 'inputs', label: '输入变量', kind: 'map', phKey: '参数名', phVal: '变量' }
  ],
  'http-request': [
    { key: 'method', label: '方法', kind: 'select', options: ['GET', 'POST', 'PUT', 'DELETE', 'PATCH'] },
    { key: 'url', label: 'URL', kind: 'text', ph: 'https://…' },
    { key: 'headers', label: 'Headers', kind: 'map', phKey: '头', phVal: '值' }
  ],
  'template-transform': [{ key: 'template', label: '模板', kind: 'textarea', ph: '文本/JSON，支持变量' }],
  'doc-extractor': [{ key: 'text', label: '文本', kind: 'textarea', ph: '待解析文本' }],
  'list-operator': [
    { key: 'list', label: '列表', kind: 'text', ph: '${节点.列表}' },
    { key: 'operation', label: '操作', kind: 'select', options: LIST_OPS },
    { key: 'count', label: '数量', kind: 'number' },
    { key: 'key', label: '字段名（sort/filter）', kind: 'text' },
    { key: 'value', label: '匹配值（filter）', kind: 'text' },
    { key: 'order', label: '排序', kind: 'select', options: ['asc', 'desc'] }
  ],
  approval: [
    { key: 'title', label: '标题', kind: 'text', ph: '待审批标题' },
    { key: 'description', label: '说明', kind: 'textarea' },
    { key: 'assignee', label: '指派给', kind: 'text', ph: '用户名/角色（留空=执行拥有者）' },
    { key: 'timeout_seconds', label: '等待超时（秒）', kind: 'number' }
  ]
}

function defaultParams(type) {
  const out = {}
  for (const f of FORMS[type] || []) {
    if (f.kind === 'strlist') out[f.key] = []
    else if (f.kind === 'map') out[f.key] = {}
    else if (f.kind === 'rows') out[f.key] = []
    else if (f.kind === 'number') out[f.key] = 0
    else if (f.kind === 'select') out[f.key] = (f.options || [''])[0]
    else out[f.key] = ''
  }
  return out
}

function ensureNodeParams(node) {
  if (!node) return
  if (!node.params || typeof node.params !== 'object') node.params = {}
  const d = defaultParams(node.type)
  for (const [k, v] of Object.entries(d)) {
    if (!(k in node.params)) node.params[k] = v
  }
  // start 输入字段：兼容旧格式（字符串）→ 富字段对象
  if (node.type === 'start' && Array.isArray(node.params.fields)) {
    node.params.fields = node.params.fields.map((f) =>
      typeof f === 'string'
        ? { key: f, label: f, type: 'text', required: false, default: '' }
        : {
            key: f?.key || '',
            label: f?.label || f?.key || '',
            type: f?.type || 'text',
            required: !!f?.required,
            default: f?.default ?? ''
          }
    )
  }
}

const p = computed(() => selectedNode.value?.data?.node?.params || {})
const formFields = computed(() => (selectedNode.value ? FORMS[selectedNode.value.type] || [] : []))

function addRow(key, row) {
  if (!Array.isArray(p.value[key])) p.value[key] = []
  p.value[key].push(row ?? '')
}

function delRow(key, i) {
  if (Array.isArray(p.value[key])) p.value[key].splice(i, 1)
}

function addMap(key) {
  if (!p.value[key] || typeof p.value[key] !== 'object') p.value[key] = {}
  let k = `key${Object.keys(p.value[key]).length + 1}`
  while (k in p.value[key]) k += '_'
  p.value[key][k] = ''
}

function delMap(key, k) {
  if (p.value[key] && k in p.value[key]) delete p.value[key][k]
}

function renameMap(key, oldK, newK) {
  const m = p.value[key]
  if (!m || !newK || oldK === newK) return
  m[newK] = m[oldK]
  delete m[oldK]
}

function syncParamsText() {
  if (!selectedNode.value) return
  paramsText.value = JSON.stringify(selectedNode.value.data.node.params || {}, null, 2)
}

onMounted(() => {
  if (props.id) {
    load(props.id)
    return
  }
  if (route.query) {
    if (route.query.name) meta.name = route.query.name
    if (route.query.version) meta.version = route.query.version
    if (route.query.description) meta.description = route.query.description
  }
  api
    .get('/workflows/templates')
    .then((t) => {
      templates.value = Array.isArray(t) ? t : []
    })
    .catch(() => {})
  markWorkflowClean()
})

async function load(id) {
  error.value = ''
  try {
    const data = await api.get(`/workflows/${id}/definition`)
    const cap = data.capability
    Object.assign(meta, {
      id: cap.id,
      name: cap.name,
      description: cap.description || '',
      version: cap.version,
      category: cap.category || '工作流',
      tags: (cap.tags || []).join(', '),
      visibility: cap.visibility,
      status: cap.status,
      author_id: cap.author_id
    })
    const wf = data.workflow || { nodes: [], edges: [] }
    flowNodes.value = (wf.nodes || []).map((n, i) => ({
      id: n.id,
      type: n.type,
      position: n.position || { x: 100 + i * 60, y: 100 + (i % 3) * 40 },
      data: { node: { ...n, id: n.id, type: n.type } }
    }))
    flowEdges.value = (wf.edges || []).map((e, i) => ({
      id: e.id || `e-${i}-${e.from}-${e.to}`,
      source: e.from,
      target: e.to,
      animated: true,
      data: e.condition ? { condition: e.condition } : {}
    }))
    wfSettings.on_error = wf.on_error || 'fail'
    wfSettings.timeout_seconds = Number(wf.timeout_seconds) || 0
    setTrigger(wf.trigger || {})
    setPresentation(wf.presentation || {})
    markWorkflowClean()
  } catch (e) {
    error.value = e.message
  }
}

function toEngineWorkflow() {
  return {
    name: meta.name.trim(),
    description: meta.description,
    version: meta.version.trim(),
    nodes: flowNodes.value.map((n) => ({
      id: n.id,
      type: n.type,
      capability: (n.data.node.capability || '').trim(),
      version: (n.data.node.version || '').trim(),
      params: n.data.node.params || {},
      timeout_seconds: Number(n.data.node.timeout_seconds) || 120,
      retries: Number(n.data.node.retries) || 0,
      position: n.position
    })),
    edges: flowEdges.value.map((e) => ({
      id: e.id,
      from: e.source,
      to: e.target,
      ...(e.data?.condition ? { condition: e.data.condition } : {})
    })),
    on_error: wfSettings.on_error,
    timeout_seconds: Number(wfSettings.timeout_seconds) || 0,
    presentation: { mode: presentation.mode },
    ...triggerToJson()
  }
}

const workflowBaseline = ref(null)

function markWorkflowClean() {
  workflowBaseline.value = JSON.stringify({
    workflow: toEngineWorkflow(),
    category: meta.category,
    tags: meta.tags,
    visibility: meta.visibility
  })
}

bindUnsavedGuard(() => {
  if (workflowBaseline.value === null) return false
  const current = JSON.stringify({
    workflow: toEngineWorkflow(),
    category: meta.category,
    tags: meta.tags,
    visibility: meta.visibility
  })
  return current !== workflowBaseline.value
})

function addNode(type, position) {
  const nid = `n${Date.now().toString(36)}${flowNodes.value.length}`
  const pos =
    position ||
    {
      x: 140 + (flowNodes.value.length % 4) * 50,
      y: 110 + Math.floor(flowNodes.value.length / 4) * 90
    }
  flowNodes.value.push({
    id: nid,
    type,
    position: pos,
    data: {
      node: {
        id: nid,
        type,
        capability: '',
        version: '',
        params: defaultParams(type),
        timeout_seconds: 120,
        retries: 0
      }
    }
  })
  selectedNodeId.value = nid
}

function onDrop(event) {
  if (!canEdit.value) return
  const type = event.dataTransfer.getData('application/wf-node-type')
  if (!type) return
  const pos = screenToFlowCoordinate({ x: event.clientX, y: event.clientY })
  addNode(type, { x: pos.x - 95, y: pos.y - 30 })
}

function onConnect(conn) {
  if (!conn.source || !conn.target || conn.source === conn.target) return
  const condition = conn.sourceHandle || ''
  if (
    flowEdges.value.some(
      (e) => e.source === conn.source && e.target === conn.target && (e.data?.condition || '') === condition
    )
  ) {
    return
  }
  flowEdges.value.push({
    id: `e-${conn.source}-${conn.target}-${Date.now().toString(36)}`,
    source: conn.source,
    target: conn.target,
    animated: true,
    data: condition ? { condition } : {}
  })
}

function onNodeClick({ node }) {
  selectedNodeId.value = node.id
}

function onPaneClick() {
  selectedNodeId.value = ''
}

function onNodesDelete(nodes) {
  const ids = new Set(nodes.map((n) => n.id))
  flowNodes.value = flowNodes.value.filter((n) => !ids.has(n.id))
  flowEdges.value = flowEdges.value.filter((e) => !ids.has(e.source) && !ids.has(e.target))
  if (ids.has(selectedNodeId.value)) selectedNodeId.value = ''
}

function onEdgesDelete(edges) {
  const ids = new Set(edges.map((e) => e.id))
  flowEdges.value = flowEdges.value.filter((e) => !ids.has(e.id))
}

function deleteSelectedNode() {
  if (!selectedNode.value) return
  onNodesDelete([selectedNode.value])
}

function deleteNodeById(id) {
  const node = flowNodes.value.find((n) => n.id === id)
  if (!node) return
  onNodesDelete([node])
}

async function ensureCaps(type) {
  if (capCache[type] !== undefined || capLoading[type]) return
  capLoading[type] = true
  capLoadError[type] = ''
  try {
    const data = await api.get(
      `/capabilities?type=${encodeURIComponent(type)}&page_size=100&sort=usage&include_components=true`
    )
    capCache[type] = data.items || []
  } catch (e) {
    capCache[type] = []
    capLoadError[type] = e.message || '加载失败'
  } finally {
    capLoading[type] = false
  }
}

function onCapChange(name) {
  const node = selectedNode.value?.data.node
  if (!node) return
  node.capability = name
  if (name && lockVersion.value) {
    const cap = (capCache[node.type] || []).find((c) => c.name === name)
    if (cap) node.version = cap.version
  }
}

function applyParams() {
  if (!selectedNode.value) return
  try {
    selectedNode.value.data.node.params = JSON.parse(paramsText.value || '{}')
    paramsError.value = ''
  } catch {
    paramsError.value = '参数 JSON 不合法，尚未生效'
  }
}

function toggleJson() {
  jsonText.value = JSON.stringify(toEngineWorkflow(), null, 2)
  jsonOpen.value = true
}

function applyWorkflow(wf) {
  flowNodes.value = (wf.nodes || []).map((n, i) => ({
    id: n.id,
    type: n.type,
    position: n.position || { x: 100 + i * 60, y: 100 },
    data: { node: { ...n, id: n.id, type: n.type } }
  }))
  flowEdges.value = (wf.edges || []).map((e, i) => ({
    id: e.id || `e-${i}`,
    source: e.from,
    target: e.to,
    animated: true,
    data: e.condition ? { condition: e.condition } : {}
  }))
  if (wf.on_error) wfSettings.on_error = wf.on_error
  if (wf.timeout_seconds !== undefined) wfSettings.timeout_seconds = wf.timeout_seconds
  setTrigger(wf.trigger || {})
  setPresentation(wf.presentation || {})
  selectedNodeId.value = ''
}

function applyJson() {
  error.value = ''
  let wf
  try {
    wf = JSON.parse(jsonText.value)
  } catch (e) {
    error.value = `JSON 不合法：${e.message}`
    return
  }
  applyWorkflow(wf)
  jsonOpen.value = false
  notice.value = '已从 JSON 加载画布'
}

function useTemplate(item) {
  if (!item?.workflow) return
  applyWorkflow(item.workflow)
  if (item.name) meta.name = item.name
  if (item.description) meta.description = item.description
  notice.value = `已载入模板：${item.name}`
}

async function save() {
  error.value = ''
  notice.value = ''
  if (!meta.name.trim()) {
    error.value = '请填写工作流名称'
    return false
  }
  const workflow = toEngineWorkflow()
  busy.value = true
  try {
    const tags = meta.tags
      .split(/[,，]/)
      .map((s) => s.trim())
      .filter(Boolean)
    if (meta.id) {
      await api.put(`/workflows/${meta.id}`, { workflow })
      await api.put(`/publish/capabilities/${meta.id}`, {
        description: meta.description,
        category: meta.category,
        tags,
        visibility: meta.visibility
      })
      notice.value = '草稿已保存'
    } else {
      const cap = await api.post('/workflows', {
        name: meta.name.trim(),
        description: meta.description,
        version: meta.version.trim(),
        category: meta.category || '工作流',
        tags,
        visibility: meta.visibility,
        workflow
      })
      meta.id = cap.id
      meta.status = cap.status
      meta.author_id = cap.author_id
      router.replace(`/workflows/${cap.id}/edit`)
      notice.value = '草稿已创建并保存'
    }
    markWorkflowClean()
    return true
  } catch (e) {
    error.value = e.message
    return false
  } finally {
    busy.value = false
  }
}

async function submitReview() {
  if (!meta.id || submitting.value) return
  submitting.value = true
  error.value = ''
  notice.value = ''
  try {
    const cap = await api.post(`/publish/capabilities/${meta.id}/submit`)
    meta.status = cap.status || 'reviewing'
    notice.value = '已提交审核'
  } catch (e) {
    error.value = e.message
  } finally {
    submitting.value = false
  }
}

async function run(inputArg) {
  error.value = ''
  notice.value = ''
  if (!meta.id) {
    const ok = await save()
    if (!ok) return
  }
  let input = inputArg
  if (input === undefined) {
    try {
      input = JSON.parse(testInput.value || '{}')
    } catch (e) {
      error.value = `测试入参不是合法 JSON：${e.message}`
      return
    }
  }
  if (!flowNodes.value.length) {
    error.value = '画布还没有节点，请先添加节点'
    return
  }
  running.value = true
  execution.value = null
  collapseAll()
  outputNodeId.value = ''
  try {
    execution.value = await api.post(`/workflows/${meta.id}/test`, { input })
    initExpanded(execution.value)
  } catch (e) {
    error.value = e.message
  } finally {
    running.value = false
  }
}

function startRun() {
  error.value = ''
  let base = {}
  try {
    base = JSON.parse(testInput.value || '{}')
  } catch {
    base = {}
  }
  for (const k of Object.keys(runForm)) delete runForm[k]
  const fields = startFieldItems.value
  if (!fields.length) {
    run(undefined)
    return
  }
  for (const f of fields)
    runForm[f.key] =
      base[f.key] !== undefined && base[f.key] !== null ? base[f.key] : f.default ?? ''
  runOpen.value = true
}

async function confirmRun() {
  const input = { ...runForm }
  runOpen.value = false
  await run(input)
}

function nodeState(id) {
  return execution.value?.node_states?.[id] || '-'
}

function nodeOutput(id) {
  const ex = execution.value
  if (!ex) return undefined
  return ex.node_outputs?.[id] ?? ex.outputs?.[id]
}

function nodeHtml(id) {
  return pickHtmlOutput(nodeOutput(id))
}

const expandedMap = reactive({})
const copiedId = ref('')

function stateClass(s) {
  if (s === 'succeeded') return 'ok'
  if (['failed', 'timeout'].includes(s)) return 'bad'
  if (['running', 'waiting'].includes(s)) return 'warn'
  if (s === 'skipped') return 'skip'
  return 'idle'
}

function stateIcon(s) {
  return { succeeded: '✓', failed: '✕', timeout: '⏱', running: '⟳', waiting: '❚❚', skipped: '—' }[s] || '○'
}

function toggleExpanded(id) {
  expandedMap[id] = !expandedMap[id]
}

function collapseAll() {
  Object.keys(expandedMap).forEach((k) => delete expandedMap[k])
}

function initExpanded(ex) {
  collapseAll()
  if (!ex) return
  const st = ex.node_states || {}
  for (const [nid, s] of Object.entries(st)) {
    if (['failed', 'timeout'].includes(s)) expandedMap[nid] = true
  }
  if ('end' in st) expandedMap.end = true
  else {
    const last = Object.keys(st).pop()
    if (last) expandedMap[last] = true
  }
}

const runDuration = computed(() => {
  const ex = execution.value
  if (!ex?.created_at || !ex?.updated_at) return ''
  const d = (new Date(ex.updated_at) - new Date(ex.created_at)) / 1000
  return d > 0 ? d.toFixed(1) : ''
})

function copyOutput(id) {
  try {
    navigator.clipboard.writeText(JSON.stringify(nodeOutput(id), null, 2))
    copiedId.value = id
    setTimeout(() => {
      if (copiedId.value === id) copiedId.value = ''
    }, 1500)
  } catch {
    copiedId.value = ''
  }
}

function stateLabel(state) {
  return {
    pending: '等待',
    running: '执行中',
    waiting: '待审批',
    succeeded: '成功',
    failed: '失败',
    timeout: '超时',
    skipped: '跳过'
  }[state] || state || '-'
}
</script>

<template>
  <div class="wf-editor">
    <div class="wf-toolbar">
      <router-link to="/my" class="btn btn-sm">← 我的能力</router-link>
      <input v-model="meta.name" class="input wf-name" :disabled="!canEdit" placeholder="工作流名称" />
      <input v-model="meta.version" class="input wf-version" :disabled="!canEdit" placeholder="0.1.0" />
      <StatusBadge v-if="meta.id" :status="meta.status" />
      <span class="muted" style="font-size: 12px; white-space: nowrap">
        {{ flowNodes.length }} 节点 · {{ flowEdges.length }} 连线
      </span>
      <div class="flex" style="margin-left: auto">
        <button class="btn btn-sm" @click="toggleJson">查看 JSON</button>
        <button v-if="canEdit" class="btn btn-sm btn-primary" :disabled="busy" @click="save">
          {{ busy ? '保存中…' : '保存草稿' }}
        </button>
        <button
          v-if="canEdit && meta.id && isOpenDraft(meta.status)"
          class="btn btn-sm btn-success"
          type="button"
          :disabled="submitting"
          @click="submitReview"
        >
          {{ submitting ? '提交中…' : '提交审核' }}
        </button>
        <button
          v-if="canTest"
          class="btn btn-sm"
          :class="{ 'btn-success': !(canEdit && meta.id && isOpenDraft(meta.status)) }"
          :disabled="running"
          @click="startRun"
        >
          {{ running ? '运行中…' : '▶ 试运行' }}
        </button>
      </div>
    </div>

    <div v-if="error" class="alert alert-error" style="margin: 0 16px 12px">{{ error }}</div>
    <div v-if="notice" class="alert alert-success" style="margin: 0 16px 12px">{{ notice }}</div>

    <div class="wf-main" :style="{ gridTemplateColumns: canEdit ? '224px 1fr 344px' : '1fr 344px' }">
      <aside v-if="canEdit" class="wf-palette">
        <div class="wf-panel-title">节点</div>
        <template v-for="g in PALETTE_GROUPS" :key="g">
          <div class="wf-palette-group">{{ g }}</div>
          <div
            v-for="p in PALETTE.filter((x) => x.group === g)"
            :key="p.type"
            class="wf-palette-item"
            draggable="true"
            @dragstart="(e) => e.dataTransfer.setData('application/wf-node-type', p.type)"
            @click="addNode(p.type)"
          >
            <span class="wf-palette-icon" :style="{ background: `${p.color}22`, color: p.color }">{{ p.icon }}</span>
            <div>
              <div class="wf-palette-name">{{ p.label }}</div>
              <div class="muted" style="font-size: 11px">{{ p.desc }}</div>
            </div>
          </div>
        </template>
        <div class="muted wf-palette-tip">
          点击或拖拽到画布添加节点，从节点底部连线到下一节点。
        </div>
      </aside>

      <div class="wf-canvas-wrap" @drop.prevent="onDrop" @dragover.prevent>
        <VueFlow
          v-model:nodes="flowNodes"
          v-model:edges="flowEdges"
          :node-types="nodeTypes"
          :fit-view-on-init="true"
          :nodes-draggable="canEdit"
          :nodes-connectable="canEdit"
          :min-zoom="0.2"
          :max-zoom="2"
          :delete-key-code="['Delete']"
          @connect="onConnect"
          @node-click="onNodeClick"
          @pane-click="onPaneClick"
          @nodes-delete="onNodesDelete"
          @edges-delete="onEdgesDelete"
        >
          <Background :gap="18" :size="1" />
          <Controls />
        </VueFlow>
        <div class="wf-start-pill">开始 — 执行入参通过 ${input.xxx} 引用</div>
      </div>

      <aside class="wf-inspector">
        <template v-if="selectedNode">
          <div class="wf-panel-title">
            <span class="wf-title-text">节点配置</span>
            <span class="wf-type-chip">{{ selectedTypeLabel }}</span>
            <button class="wf-close" @click="selectedNodeId = ''">✕</button>
          </div>

          <template v-if="isMarketNode">
            <div class="insp-sec">能力</div>
            <div class="field">
              <label>{{ TYPE_LABELS[selectedNode.type] }}（市场已发布）</label>
              <div class="cap-select-row">
                <select
                  :value="selectedNode.data.node.capability"
                  class="select"
                  :disabled="!canEdit || capLoading[selectedNode.type]"
                  @focus="ensureCaps(selectedNode.type)"
                  @change="onCapChange($event.target.value)"
                >
                  <option value="">{{ capLoading[selectedNode.type] ? '加载中…' : '请选择…' }}</option>
                  <option
                    v-if="selectedNode.data.node.capability && !selectedCaps.some((c) => c.name === selectedNode.data.node.capability)"
                    :value="selectedNode.data.node.capability"
                  >{{ selectedNode.data.node.capability }}（当前）</option>
                  <option v-for="cap in selectedCaps" :key="cap.id" :value="cap.name">
                    {{ cap.name }} · v{{ cap.version }}
                  </option>
                </select>
                <input
                  v-model="selectedNode.data.node.version"
                  class="input cap-ver"
                  :disabled="!canEdit"
                  placeholder="最新"
                  title="留空 = 用最新版本"
                />
              </div>
              <label class="checkbox">
                <input v-model="lockVersion" type="checkbox" :disabled="!canEdit" />
                选择能力后锁定其版本
              </label>
              <div v-if="capLoadError[selectedNode.type]" class="alert alert-error cap-empty">
                {{ capLoadError[selectedNode.type] }}（请确认已登录且后端在运行）
              </div>
              <div v-else-if="!capLoading[selectedNode.type] && selectedCaps.length === 0" class="muted cap-empty">
                暂无已发布的 {{ TYPE_LABELS[selectedNode.type] }}，请先在「我的能力」发布并审核通过。
              </div>
            </div>
          </template>

          <div v-if="formFields.length || selectedNode.type === 'tool'" class="insp-sec">参数</div>
          <div v-for="f in formFields" :key="f.key" class="field">
            <label>{{ f.label }}</label>

            <VarInput
              v-if="f.kind === 'text'"
              v-model="p[f.key]"
              :disabled="!canEdit"
              :placeholder="f.ph || ''"
              :variables="flatVars"
            />
            <VarInput
              v-else-if="f.kind === 'textarea'"
              v-model="p[f.key]"
              multiline
              :disabled="!canEdit"
              :placeholder="f.ph || ''"
              :variables="flatVars"
            />
            <input
              v-else-if="f.kind === 'number'"
              v-model.number="p[f.key]"
              type="number"
              class="input"
              :disabled="!canEdit"
            />
            <select v-else-if="f.kind === 'select'" v-model="p[f.key]" class="select" :disabled="!canEdit">
              <option v-for="o in f.options" :key="o" :value="o">{{ o }}</option>
            </select>

            <div v-else-if="f.kind === 'strlist'">
              <div v-for="i in (p[f.key] || []).length" :key="i" class="kv-row">
                <VarInput v-model="p[f.key][i - 1]" :disabled="!canEdit" :placeholder="f.ph || ''" :variables="flatVars" />
                <button v-if="canEdit" class="btn btn-sm" type="button" @click="delRow(f.key, i - 1)">×</button>
              </div>
              <button v-if="canEdit" class="btn btn-sm" type="button" @click="addRow(f.key, '')">+ 新增</button>
            </div>

            <div v-else-if="f.kind === 'map'">
              <div v-for="k in Object.keys(p[f.key] || {})" :key="k" class="kv-row">
                <input
                  class="input"
                  :value="k"
                  :disabled="!canEdit"
                  :placeholder="f.phKey || '键'"
                  @change="renameMap(f.key, k, $event.target.value)"
                />
                <VarInput
                  v-if="Array.isArray(p[f.key][k])"
                  :model-value="(p[f.key][k] || []).join(',')"
                  :disabled="!canEdit"
                  :placeholder="f.phVal || '值（多项用逗号分隔）'"
                  :variables="flatVars"
                  @update:model-value="(v) => (p[f.key][k] = String(v).split(',').map((s) => s.trim()).filter(Boolean))"
                />
                <VarInput
                  v-else
                  v-model="p[f.key][k]"
                  :disabled="!canEdit"
                  :placeholder="f.phVal || '值'"
                  :variables="flatVars"
                />
                <button v-if="canEdit" class="btn btn-sm" type="button" @click="delMap(f.key, k)">×</button>
              </div>
              <button v-if="canEdit" class="btn btn-sm" type="button" @click="addMap(f.key)">+ 新增</button>
            </div>

            <div v-else-if="f.kind === 'rows'" :class="{ 'rows-grid': f.grid }">
              <div v-for="(row, i) in (p[f.key] || [])" :key="i" class="kv-row">
                <template v-for="rf in f.rowFields" :key="rf.key">
                  <select v-if="rf.kind === 'select'" v-model="row[rf.key]" class="select" :disabled="!canEdit">
                    <option v-for="o in rf.options" :key="o" :value="o">{{ o }}</option>
                  </select>
                  <label v-else-if="rf.kind === 'bool'" class="checkbox" style="margin-top: 0; flex: none">
                    <input v-model="row[rf.key]" type="checkbox" :disabled="!canEdit" /> 必填
                  </label>
                  <VarInput v-else v-model="row[rf.key]" :disabled="!canEdit" :placeholder="rf.ph || rf.key" :variables="flatVars" :no-vars="!!rf.noVars" />
                </template>
                <button v-if="canEdit" class="btn btn-sm" type="button" @click="delRow(f.key, i)">×</button>
              </div>
              <button v-if="canEdit" class="btn btn-sm" type="button" @click="addRow(f.key, f.newRow())">+ 新增</button>
            </div>

            <CodeEditor
              v-else-if="f.kind === 'code'"
              :model-value="p[f.key]"
              language="python"
              compact
              :height="220"
              :readonly="!canEdit"
              @update:model-value="(v) => (p[f.key] = v)"
            />
          </div>
          <div v-if="selectedNode.type === 'tool'" class="muted" style="font-size: 12px">
            工具入参请用下方「高级 JSON」（依该工具 schema 填写）。
          </div>

          <template v-if="selectedNode.type === 'start'">
            <div class="insp-sec">触发器</div>
            <div class="field">
              <label>触发方式</label>
              <select v-model="trigger.type" class="select" :disabled="!canEdit">
                <option v-for="(label, key) in TRIGGER_LABELS" :key="key" :value="key">{{ label }}</option>
              </select>
            </div>

            <template v-if="trigger.type === 'webhook' || trigger.type === 'gitlab'">
              <div class="field">
                <label>{{ trigger.type === 'gitlab' ? 'GitLab Secret Token' : 'Webhook Token' }}</label>
                <div class="kv-row">
                  <input v-model="trigger.token" class="input" :disabled="!canEdit" placeholder="点击生成或自定义" />
                  <button v-if="canEdit" class="btn btn-sm" type="button" @click="trigger.token = genToken()">生成</button>
                </div>
              </div>
              <div v-if="triggerUrl" class="field">
                <label>回调地址</label>
                <div class="trigger-url">{{ triggerUrl }}</div>
                <div v-if="trigger.type === 'gitlab'" class="muted" style="font-size: 11px; margin-top: 4px">
                  GitLab 项目 Settings → Webhooks 填此 URL，Secret token 填上方值，勾选 Push / Merge request events。
                </div>
                <div v-else class="muted" style="font-size: 11px; margin-top: 4px">
                  POST 此地址，请求头 X-Workflow-Token，body {"input": {...}}。
                </div>
              </div>
            </template>

            <template v-else-if="trigger.type === 'schedule'">
              <div class="field">
                <label>调度（cron：分 时 日 月 周）</label>
                <div class="kv-row">
                  <input v-model="trigger.cron" class="input" :disabled="!canEdit" placeholder="0 9 * * *" />
                  <label class="checkbox"><input v-model="trigger.enabled" type="checkbox" :disabled="!canEdit" /> 启用</label>
                </div>
                <div class="var-chips" style="margin-top: 6px">
                  <button
                    v-for="c in CRON_PRESETS"
                    :key="c.value"
                    class="chip"
                    type="button"
                    :disabled="!canEdit"
                    @click="trigger.cron = c.value"
                  >{{ c.label }}</button>
                </div>
              </div>
              <div class="field">
                <label>触发入参（JSON）</label>
                <CodeEditor v-model="trigger.inputText" language="json" compact :height="120" :readonly="!canEdit" />
              </div>
            </template>

            <div v-if="trigger.type !== 'none'" class="muted" style="font-size: 11px; margin: -6px 0 10px">
              触发仅对「已发布」版本生效。
            </div>
          </template>

          <details class="json-adv" @toggle="(e) => e.target.open && syncParamsText()">
            <summary>高级：直接编辑 JSON</summary>
            <div class="field" style="margin-top: 8px">
              <CodeEditor
                v-model="paramsText"
                language="json"
                compact
                :height="220"
                :readonly="!canEdit"
                @update:model-value="applyParams"
              />
              <div v-if="paramsError" class="muted" style="color: var(--warning); font-size: 12px">{{ paramsError }}</div>
            </div>
          </details>

          <div class="insp-sec">运行</div>
          <div class="field-row">
            <div class="field">
              <label>超时（秒）</label>
              <input v-model.number="selectedNode.data.node.timeout_seconds" type="number" class="input" :disabled="!canEdit" min="1" />
            </div>
            <div class="field">
              <label>重试次数</label>
              <input v-model.number="selectedNode.data.node.retries" type="number" class="input" :disabled="!canEdit" min="0" />
            </div>
          </div>

          <button v-if="canEdit" class="btn btn-danger btn-block mt-12" @click="deleteSelectedNode">删除节点</button>
        </template>

        <template v-else>
          <div class="wf-panel-title">工作流设置</div>

          <div class="field">
            <label>描述</label>
            <textarea v-model="meta.description" class="textarea" rows="3" :disabled="!canEdit" placeholder="这个工作流做什么"></textarea>
          </div>

          <div class="field">
            <label>分类</label>
            <select v-model="meta.category" class="select" :disabled="!canEdit">
              <option value="">不选</option>
              <option v-for="c in (TYPE_CATEGORIES.workflow || ['工作流', '数据分析', '自动化流程', '业务处理', '通用流程'])" :key="c" :value="c">{{ c }}</option>
            </select>
          </div>

          <div class="field-row">
            <div class="field">
              <label>标签（逗号分隔）</label>
              <input v-model="meta.tags" class="input" :disabled="!canEdit" placeholder="如：报表, 自动化" />
            </div>
            <div class="field">
              <label>可见性</label>
              <select v-model="meta.visibility" class="select" :disabled="!canEdit">
                <option value="internal">内部（全员可见）</option>
                <option value="public">公开</option>
                <option value="team">团队</option>
                <option value="private">私有（仅自己）</option>
              </select>
            </div>
          </div>

          <div class="field">
            <label>对外形态</label>
            <select v-model="presentation.mode" class="select" :disabled="!canEdit">
              <option v-for="o in PRESENTATION_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
            </select>
            <div class="muted" style="font-size: 11px; margin-top: 4px">
              决定能力详情页「使用」区块以 表单 / 对话 / 自动化 呈现。
            </div>
          </div>

          <div class="field-row">
            <div class="field">
              <label>节点失败策略</label>
              <select v-model="wfSettings.on_error" class="select" :disabled="!canEdit">
                <option value="fail">失败即停止</option>
                <option value="continue">失败继续执行</option>
              </select>
            </div>
            <div class="field">
              <label>整体超时（秒，0=不限）</label>
              <input v-model.number="wfSettings.timeout_seconds" type="number" class="input" :disabled="!canEdit" min="0" />
            </div>
          </div>

          <div v-if="canTest" class="field">
            <label>测试入参（JSON，试运行时传入）</label>
            <CodeEditor v-model="testInput" language="json" compact :height="200" />
          </div>

          <div v-if="canEdit && templates.length && !meta.id" class="field">
            <label>从模板新建</label>
            <div class="var-chips">
              <button v-for="t in templates" :key="t.name" class="chip" type="button" @click="useTemplate(t)">
                {{ t.name }}
              </button>
            </div>
          </div>

          <div class="muted" style="font-size: 12px; line-height: 1.8">
            <div>• 从左侧添加节点，拖拽节点底部手柄连接上下游；分支节点（条件/分类）有多个输出口</div>
            <div>• 市场能力节点引用已发布的 tool / agent / skill / mcp；连接器支持 op=call 调用工具</div>
            <div>• 变量：${input.字段} 引用入参；Dify 语法以 #node.field#（双花括号包裹）引用上游输出</div>
            <div v-if="canEdit && meta.id && isOpenDraft(meta.status)">• 保存后为草稿，可在上方提交审核，通过后才会上架</div>
            <div v-else>• 保存后为草稿。要提交审核，请到「我的能力」操作，通过后才会上架</div>
          </div>
        </template>
      </aside>
    </div>

    <div v-if="execution" class="wf-result wf-run panel">
      <div class="wf-run-head">
        <div class="wf-run-head-left">
          <span class="wf-run-title">试运行结果</span>
          <span class="wf-run-state" :class="stateClass(execution.state)">
            <span class="wf-run-dot"></span>{{ stateLabel(execution.state) }}
          </span>
          <span class="muted" style="font-size: 12px">{{ formatDate(execution.updated_at) }}</span>
          <span v-if="runDuration" class="muted" style="font-size: 12px">耗时 {{ runDuration }}s</span>
        </div>
        <button class="btn btn-sm" type="button" @click="collapseAll">全部收起</button>
      </div>
      <div v-if="execution.state === 'waiting'" class="wf-wait-hint">
        <span>流程已暂停在「人工审批」节点，等待决定。</span>
        <router-link to="/approvals" class="btn btn-sm btn-primary">去审批中心</router-link>
      </div>
      <div v-if="execution.error" class="alert alert-error mt-12">{{ execution.error }}</div>
      <div class="wf-trace">
        <div v-for="n in flowNodes" :key="n.id" class="wf-trace-item">
          <button type="button" class="wf-trace-row" @click="toggleExpanded(n.id)">
            <span
              class="wf-trace-icon"
              :class="[stateClass(nodeState(n.id)), { spin: nodeState(n.id) === 'running' }]"
            >{{ stateIcon(nodeState(n.id)) }}</span>
            <span class="wf-trace-name">{{ n.data.node.capability || n.id }}</span>
            <span class="wf-trace-id">{{ n.id }}</span>
            <span class="wf-trace-state">{{ stateLabel(nodeState(n.id)) }}</span>
            <span class="wf-trace-caret">{{ expandedMap[n.id] ? '▾' : '▸' }}</span>
          </button>
          <div v-if="expandedMap[n.id]" class="wf-trace-body">
            <div v-if="nodeOutput(n.id) === undefined" class="muted" style="font-size: 12px; padding: 6px 0">无输出</div>
            <template v-else>
              <div class="wf-trace-actions">
                <button class="btn btn-sm" type="button" @click="copyOutput(n.id)">
                  {{ copiedId === n.id ? '已复制' : '复制输出' }}
                </button>
                <HtmlPreview v-if="nodeHtml(n.id)" :html="nodeHtml(n.id)" />
              </div>
              <pre class="wf-json">{{ JSON.stringify(nodeOutput(n.id), null, 2) }}</pre>
            </template>
          </div>
        </div>
      </div>
    </div>

    <div v-if="jsonOpen" class="modal-mask" @click.self="jsonOpen = false">
      <div class="modal panel">
        <div class="modal-header">
          <h3 style="margin: 0">workflow.json</h3>
          <button class="modal-close" @click="jsonOpen = false">✕</button>
        </div>
        <CodeEditor v-model="jsonText" language="json" compact height="min(70vh, 720px)" />
        <div class="modal-foot">
          <span class="muted" style="font-size: 12px">可直接编辑后应用；引擎执行时忽略 position 字段</span>
          <div class="flex">
            <button class="btn" @click="jsonOpen = false">取消</button>
            <button class="btn btn-primary" @click="applyJson">应用 JSON</button>
          </div>
        </div>
      </div>
    </div>

    <div v-if="runOpen" class="modal-mask" @click.self="runOpen = false">
      <div class="modal panel" style="width: 560px">
        <div class="modal-header">
          <h3 style="margin: 0">试运行 · 输入参数</h3>
          <button class="modal-close" @click="runOpen = false">✕</button>
        </div>
        <div class="muted" style="font-size: 12px; margin-bottom: 12px">
          按「开始」节点声明的输入字段填写（在流程里以 ${input.字段} 引用）。
        </div>
        <div v-for="f in startFieldItems" :key="f.key" class="field">
          <label>{{ f.label || f.key }}</label>
          <input v-model="runForm[f.key]" class="input" :placeholder="f.placeholder || f.key" />
        </div>
        <div v-if="!startFieldItems.length" class="muted" style="font-size: 12px">
          开始节点未声明输入字段，将按空参运行。
        </div>
        <div class="modal-foot">
          <span class="muted" style="font-size: 12px">留空即传入空字符串</span>
          <div class="flex">
            <button class="btn" type="button" @click="runOpen = false">取消</button>
            <button class="btn btn-primary" type="button" :disabled="running" @click="confirmRun">
              {{ running ? '运行中…' : '▶ 运行' }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <div v-if="outputNodeId" class="modal-mask" @click.self="outputNodeId = ''">
      <div class="modal panel" style="width: 760px; max-height: 86vh; overflow: auto">
        <div class="modal-header">
          <h3 style="margin: 0">
            节点输出 · {{ outputNodeId }}
            <span class="muted" style="font-size: 12px; font-weight: 400">{{ stateLabel(nodeState(outputNodeId)) }}</span>
          </h3>
          <button class="modal-close" @click="outputNodeId = ''">✕</button>
        </div>
        <div v-if="nodeOutput(outputNodeId) === undefined" class="muted" style="font-size: 12px">
          本次运行该节点无输出
        </div>
        <template v-else>
          <div class="wf-trace-actions">
            <button class="btn btn-sm" type="button" @click="copyOutput(outputNodeId)">
              {{ copiedId === outputNodeId ? '已复制' : '复制输出' }}
            </button>
            <HtmlPreview v-if="nodeHtml(outputNodeId)" :html="nodeHtml(outputNodeId)" />
          </div>
          <pre class="wf-json">{{ JSON.stringify(nodeOutput(outputNodeId), null, 2) }}</pre>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.wf-editor { height: calc(100vh - 58px); display: flex; flex-direction: column; }
.wf-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--border);
  background: var(--panel);
  flex-wrap: wrap;
}
.wf-name { max-width: 260px; font-weight: 600; }
.wf-version { max-width: 90px; }
.wf-main {
  flex: 1;
  display: grid;
  grid-template-columns: 224px 1fr 344px;
  min-height: 0;
}
.wf-palette {
  border-right: 1px solid var(--border);
  background: var(--panel);
  padding: 14px 12px;
  overflow: auto;
}
.wf-panel-title {
  position: sticky;
  top: -14px;
  z-index: 6;
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  font-size: 14px;
  margin: 0 0 6px;
  padding: 12px 0 10px;
  background: var(--panel);
  border-bottom: 1px solid var(--border);
}
.wf-title-text { font-weight: 600; }
.wf-type-chip {
  font-size: 11px;
  font-weight: 500;
  color: var(--primary);
  background: var(--primary-soft, #eef3ff);
  border-radius: 999px;
  padding: 2px 9px;
}
.wf-close { margin-left: auto; background: none; border: none; color: var(--muted); cursor: pointer; font-size: 14px; }
.wf-close:hover { color: var(--text); }
.wf-palette-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 10px;
  margin-bottom: 7px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--panel-2);
  cursor: grab;
  transition: border-color 0.15s ease, transform 0.12s ease, box-shadow 0.15s ease;
}
.wf-palette-item:hover {
  border-color: var(--primary);
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(47, 107, 255, 0.12);
}
.wf-palette-item:active { cursor: grabbing; }
.wf-palette-icon {
  width: 34px; height: 34px; border-radius: 9px;
  display: flex; align-items: center; justify-content: center; font-size: 16px; flex-shrink: 0;
}
.wf-palette-name { font-weight: 600; font-size: 13px; }
.wf-palette-group {
  font-size: 11px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase;
  color: var(--muted); margin: 12px 2px 6px;
}
.wf-palette-group:first-child { margin-top: 0; }
.wf-palette-tip { font-size: 11px; line-height: 1.7; padding: 8px 2px; }
.wf-canvas-wrap {
  position: relative;
  background:
    radial-gradient(circle at 50% 50%, rgba(79, 140, 255, 0.04), transparent 70%),
    var(--bg);
  min-width: 0;
  min-height: 0;
}
.wf-start-pill {
  position: absolute;
  top: 12px;
  left: 14px;
  z-index: 5;
  padding: 6px 12px;
  border-radius: 999px;
  background: var(--panel);
  border: 1px solid var(--border);
  color: var(--muted);
  font-size: 12px;
  pointer-events: none;
}
.wf-inspector {
  border-left: 1px solid var(--border);
  background: var(--panel);
  padding: 14px;
  overflow: auto;
}
.cap-list { max-height: 180px; overflow: auto; margin-top: 6px; border: 1px solid var(--border); border-radius: 8px; }
.cap-item {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  padding: 7px 10px;
  background: none;
  border: none;
  border-bottom: 1px solid var(--border);
  color: var(--text);
  cursor: pointer;
  text-align: left;
  font-size: 12px;
}
.cap-item:last-child { border-bottom: none; }
.cap-item:hover { background: var(--panel-2); }
.cap-item.active { background: rgba(79, 140, 255, 0.12); color: #2451c7; }
.cap-item:disabled { cursor: default; opacity: 0.75; }
.cap-empty { padding: 10px; font-size: 12px; }
.var-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip {
  padding: 3px 9px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--panel-2);
  color: #2451c7;
  font-size: 11px;
  cursor: pointer;
  font-family: monospace;
}
.chip:hover { border-color: var(--primary); }
.chip-up { color: #7ce3ab; }
.checkbox { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--muted); margin-top: 8px; cursor: pointer; }
.checkbox input { margin: 0; }
.code { font-family: 'Cascadia Code', Consolas, monospace; font-size: 12px; }
.btn-block { width: 100%; }
.wf-result {
  margin: 14px 16px 16px;
  max-height: 460px;
  overflow: auto;
  flex-shrink: 0;
}
.wf-run { padding: 12px 14px; }
.wf-run-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.wf-run-head-left { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.wf-run-title { font-weight: 600; font-size: 14px; }
.wf-run-state {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 12px; padding: 2px 9px; border-radius: 999px;
  border: 1px solid var(--border); color: var(--muted);
}
.wf-run-state .wf-run-dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
.wf-run-state.ok { color: #16a34a; border-color: rgba(22, 163, 74, 0.4); background: rgba(22, 163, 74, 0.08); }
.wf-run-state.bad { color: #dc2626; border-color: rgba(220, 38, 38, 0.4); background: rgba(220, 38, 38, 0.08); }
.wf-run-state.warn { color: #d97706; border-color: rgba(217, 119, 6, 0.4); background: rgba(217, 119, 6, 0.08); }
.wf-trace { margin-top: 12px; border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }
.wf-trace-item + .wf-trace-item { border-top: 1px solid var(--border); }
.wf-trace-row {
  display: flex; align-items: center; gap: 10px; width: 100%;
  background: transparent; border: 0; padding: 9px 12px; cursor: pointer;
  text-align: left; color: inherit; font: inherit;
}
.wf-trace-row:hover { background: var(--panel-2); }
.wf-trace-icon {
  flex: none; width: 18px; height: 18px; border-radius: 50%;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 11px; color: #fff; background: var(--muted); line-height: 1;
}
.wf-trace-icon.ok { background: #16a34a; }
.wf-trace-icon.bad { background: #dc2626; }
.wf-trace-icon.warn { background: #d97706; }
.wf-trace-icon.spin { animation: wfspin 1s linear infinite; }
@keyframes wfspin { to { transform: rotate(360deg); } }
.wf-trace-name { font-size: 13px; font-weight: 500; }
.wf-trace-id { font-size: 11px; color: var(--muted); font-family: 'Cascadia Code', Consolas, monospace; }
.wf-trace-state { margin-left: auto; font-size: 12px; color: var(--muted); }
.wf-trace-caret { color: var(--muted); font-size: 11px; width: 12px; text-align: center; }
.wf-trace-body { padding: 2px 12px 12px 40px; background: var(--panel-2); }
.wf-trace-actions { display: flex; align-items: flex-start; gap: 8px; flex-wrap: wrap; margin: 2px 0 4px; }
.wf-json {
  margin: 4px 0 0;
  padding: 8px;
  background: var(--bg);
  border-radius: 6px;
  border: 1px solid var(--border);
  font-size: 11px;
  overflow: auto;
  max-height: 280px;
}
.modal-mask {
  position: fixed; inset: 0; background: var(--overlay, rgba(15, 23, 42, 0.45)); z-index: 100;
  display: flex; align-items: center; justify-content: center; padding: 20px;
}
.modal { width: 960px; max-width: 100%; max-height: 94vh; overflow: auto; }
.modal-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.modal-close { background: none; border: none; color: var(--muted); font-size: 16px; cursor: pointer; }
.modal-foot { display: flex; justify-content: space-between; align-items: center; margin-top: 14px; }
.kv-row { display: flex; gap: 6px; align-items: flex-start; margin-bottom: 6px; }
.kv-row > .input, .kv-row .select { flex: 1 1 0; min-width: 0; width: auto; }
.kv-row .var-input { flex: 1 1 0; min-width: 0; width: auto; }
.kv-row .btn-sm { flex: none; padding: 4px 8px; }
.rows-grid { overflow-x: auto; padding-bottom: 6px; }
.rows-grid .kv-row { display: flex; flex-wrap: nowrap; width: max-content; min-width: 100%; gap: 6px; align-items: center; }
.rows-grid .kv-row > * { flex: 0 0 auto; }
.rows-grid .kv-row .var-input { width: 140px; }
.rows-grid .kv-row > .var-input:first-child { width: 168px; }
.rows-grid .kv-row .select { width: 104px; }
.rows-grid .kv-row .checkbox { margin-top: 0; white-space: nowrap; }
.json-adv { margin-top: 14px; }
.json-adv > summary {
  cursor: pointer;
  font-size: 12px;
  color: var(--muted);
  padding: 4px 0;
  user-select: none;
}
.json-adv[open] > summary { color: var(--text); }
.insp-sec {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--muted);
  text-transform: uppercase;
  margin: 16px 0 10px;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--border);
}
.insp-sec:first-child { margin-top: 4px; }
.wf-wait-hint {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  margin-top: 14px;
  padding: 10px 14px;
  border-radius: 10px;
  font-size: 13px;
  color: #b7791f;
  background: rgba(245, 165, 36, 0.12);
  border: 1px solid #f5dfb0;
}
.cap-select-row { display: flex; gap: 8px; align-items: center; }
.cap-select-row .select { flex: 1; min-width: 0; }
.cap-ver { flex: none; width: 84px; }
.trigger-url {
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 11px;
  word-break: break-all;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 8px;
  color: #2451c7;
}
</style>
