<script setup>
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { VueFlow, useVueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { api } from '../api'
import { authState } from '../stores/auth'
import { TYPE_CATEGORIES, TYPE_LABELS, formatDate } from '../utils/format'
import StatusBadge from '../components/StatusBadge.vue'
import WorkflowNode from '../components/workflow/WorkflowNode.vue'

const props = defineProps({ id: { type: String, default: '' } })
const route = useRoute()
const router = useRouter()
const { screenToFlowCoordinate } = useVueFlow()

const nodeTypes = { tool: WorkflowNode, agent: WorkflowNode, skill: WorkflowNode, mcp: WorkflowNode }

const PALETTE = [
  { type: 'tool', label: '工具', desc: '调用市场工具', color: '#4f8cff', icon: '🔧' },
  { type: 'agent', label: 'Agent', desc: '委派 A2A Agent', color: '#9d6bff', icon: '🤖' },
  { type: 'skill', label: '技能', desc: '激活执行技能', color: '#2fbf71', icon: '📘' },
  { type: 'mcp', label: 'MCP', desc: '安装 MCP 连接', color: '#e2a93b', icon: '🔌' }
]

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
const flowNodes = ref([])
const flowEdges = ref([])
const selectedNodeId = ref('')
const capCache = reactive({})
const capSearch = ref('')
const paramsText = ref('{}')
const paramsError = ref('')
const paramsTextarea = ref(null)
const lockVersion = ref(true)

const jsonOpen = ref(false)
const jsonText = ref('')
const testInput = ref('{\n  "query": ""\n}')
const execution = ref(null)

const error = ref('')
const notice = ref('')
const busy = ref(false)
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
const selectedNode = computed(() =>
  flowNodes.value.find((n) => n.id === selectedNodeId.value)
)
const selectedCaps = computed(() => capCache[selectedNode.value?.type] || [])
const filteredCaps = computed(() => {
  const q = capSearch.value.trim().toLowerCase()
  const list = selectedCaps.value
  if (!q) return list
  return list.filter((c) => `${c.name} ${c.description || ''}`.toLowerCase().includes(q))
})
const inputVars = computed(() => {
  const vars = ['${input}']
  try {
    const obj = JSON.parse(testInput.value || '{}')
    Object.keys(obj).forEach((k) => vars.push(`\${input.${k}}`))
  } catch {
    // 忽略非法 JSON，仅给通用变量
  }
  return vars
})
const upstreamVars = computed(() => {
  if (!selectedNode.value) return []
  return flowNodes.value
    .filter((n) => n.id !== selectedNode.value.id)
    .flatMap((n) => [`\${${n.id}}`, `\${${n.id}.output}`])
})

watch(
  () => selectedNodeId.value,
  () => {
    if (selectedNode.value) {
      paramsText.value = JSON.stringify(selectedNode.value.data.node.params || {}, null, 2)
      paramsError.value = ''
    }
  }
)

onMounted(() => {
  if (props.id) {
    load(props.id)
  } else if (route.query) {
    if (route.query.name) meta.name = route.query.name
    if (route.query.version) meta.version = route.query.version
    if (route.query.description) meta.description = route.query.description
  }
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
      animated: true
    }))
    wfSettings.on_error = wf.on_error || 'fail'
    wfSettings.timeout_seconds = Number(wf.timeout_seconds) || 0
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
    edges: flowEdges.value.map((e) => ({ id: e.id, from: e.source, to: e.target })),
    on_error: wfSettings.on_error,
    timeout_seconds: Number(wfSettings.timeout_seconds) || 0
  }
}

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
        params: {},
        timeout_seconds: 120,
        retries: 0
      }
    }
  })
  selectedNodeId.value = nid
  capSearch.value = ''
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
  if (flowEdges.value.some((e) => e.source === conn.source && e.target === conn.target)) return
  flowEdges.value.push({
    id: `e-${conn.source}-${conn.target}-${Date.now().toString(36)}`,
    source: conn.source,
    target: conn.target,
    animated: true
  })
}

function onNodeClick({ node }) {
  selectedNodeId.value = node.id
  capSearch.value = ''
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

async function ensureCaps(type) {
  if (capCache[type] !== undefined) return
  try {
    const data = await api.get(`/capabilities?type=${type}&page_size=100&sort=usage`)
    capCache[type] = data.items || []
  } catch {
    capCache[type] = []
  }
}

function pickCap(cap) {
  const node = selectedNode.value?.data.node
  if (!node) return
  node.capability = cap.name
  if (lockVersion.value) node.version = cap.version
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

function insertVar(expr) {
  const el = paramsTextarea.value
  const start = el ? el.selectionStart : paramsText.value.length
  const end = el ? el.selectionEnd : paramsText.value.length
  paramsText.value = paramsText.value.slice(0, start) + expr + paramsText.value.slice(end)
  nextTick(() => {
    if (el) {
      const pos = start + expr.length
      el.focus()
      el.setSelectionRange(pos, pos)
    }
  })
}

function toggleJson() {
  jsonText.value = JSON.stringify(toEngineWorkflow(), null, 2)
  jsonOpen.value = true
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
    animated: true
  }))
  if (wf.on_error) wfSettings.on_error = wf.on_error
  if (wf.timeout_seconds !== undefined) wfSettings.timeout_seconds = wf.timeout_seconds
  jsonOpen.value = false
  notice.value = '已从 JSON 加载画布'
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
    return true
  } catch (e) {
    error.value = e.message
    return false
  } finally {
    busy.value = false
  }
}

async function run() {
  error.value = ''
  notice.value = ''
  if (!meta.id) {
    const ok = await save()
    if (!ok) return
  }
  let input = {}
  try {
    input = JSON.parse(testInput.value || '{}')
  } catch (e) {
    error.value = `测试入参不是合法 JSON：${e.message}`
    return
  }
  if (!flowNodes.value.length) {
    error.value = '画布还没有节点，请先添加节点'
    return
  }
  running.value = true
  execution.value = null
  try {
    execution.value = await api.post(`/workflows/${meta.id}/test`, { input })
  } catch (e) {
    error.value = e.message
  } finally {
    running.value = false
  }
}

function nodeState(id) {
  return execution.value?.node_states?.[id] || '-'
}

function stateLabel(state) {
  return {
    pending: '等待',
    running: '执行中',
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
        <button v-if="canTest" class="btn btn-sm btn-success" :disabled="running" @click="run">
          {{ running ? '运行中…' : '▶ 试运行' }}
        </button>
      </div>
    </div>

    <div v-if="error" class="alert alert-error" style="margin: 0 16px 12px">{{ error }}</div>
    <div v-if="notice" class="alert alert-success" style="margin: 0 16px 12px">{{ notice }}</div>

    <div class="wf-main" :style="{ gridTemplateColumns: canEdit ? '210px 1fr 330px' : '1fr 330px' }">
      <aside v-if="canEdit" class="wf-palette">
        <div class="wf-panel-title">节点</div>
        <div
          v-for="p in PALETTE"
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
            节点配置 · {{ TYPE_LABELS[selectedNode.type] }}
            <button class="wf-close" @click="selectedNodeId = ''">✕</button>
          </div>

          <div class="field">
            <label>能力（市场已发布）</label>
            <input
              v-model="capSearch"
              class="input"
              placeholder="搜索能力名称…"
              :disabled="!canEdit"
              @focus="ensureCaps(selectedNode.type)"
            />
            <div class="cap-list">
              <button
                v-for="cap in filteredCaps"
                :key="cap.id"
                class="cap-item"
                :class="{ active: cap.name === selectedNode.data.node.capability }"
                :disabled="!canEdit"
                @click="pickCap(cap)"
              >
                <span>{{ cap.name }}</span>
                <span class="muted">v{{ cap.version }} · {{ cap.usage_count }}次</span>
              </button>
              <div v-if="filteredCaps.length === 0" class="muted cap-empty">
                暂无已发布的{{ TYPE_LABELS[selectedNode.type] }}能力
              </div>
            </div>
          </div>

          <div class="field-row">
            <div class="field">
              <label>版本（留空 = 最新）</label>
              <input v-model="selectedNode.data.node.version" class="input" :disabled="!canEdit" placeholder="1.0.0" />
            </div>
            <div class="field">
              <label>锁版本</label>
              <label class="checkbox">
                <input v-model="lockVersion" type="checkbox" :disabled="!canEdit" />
                选择能力时锁定
              </label>
            </div>
          </div>

          <div class="field">
            <label>参数（JSON，支持变量引用）</label>
            <textarea
              ref="paramsTextarea"
              v-model="paramsText"
              class="textarea code"
              rows="6"
              spellcheck="false"
              :disabled="!canEdit"
              @input="applyParams"
            ></textarea>
            <div v-if="paramsError" class="muted" style="color: var(--warning); font-size: 12px">{{ paramsError }}</div>
          </div>

          <div class="field">
            <div class="muted" style="font-size: 12px; margin-bottom: 6px">可用变量（点击插入）</div>
            <div class="var-chips">
              <button v-for="v in inputVars" :key="v" class="chip" :disabled="!canEdit" @click="insertVar(v)">{{ v }}</button>
              <button v-for="v in upstreamVars" :key="v" class="chip chip-up" :disabled="!canEdit" @click="insertVar(v)">{{ v }}</button>
            </div>
            <div class="muted" style="font-size: 11px; margin-top: 6px">
              ${input.字段} 引用测试入参；${节点id.字段} 引用上游输出（如 ${t1.output}）
            </div>
          </div>

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

          <button v-if="canEdit" class="btn btn-danger btn-block" @click="deleteSelectedNode">删除节点</button>
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
            <textarea v-model="testInput" class="textarea code" rows="5" spellcheck="false"></textarea>
          </div>

          <div class="muted" style="font-size: 12px; line-height: 1.8">
            <div>• 从左侧添加节点，拖拽节点底部手柄连接上下游</div>
            <div>• 节点引用市场上已发布的 tool / agent / skill / mcp 能力</div>
            <div>• 保存后为草稿，可在「我的能力」提交审核</div>
          </div>
        </template>
      </aside>
    </div>

    <div v-if="execution" class="wf-result panel">
      <div class="flex-between flex-wrap">
        <h3 style="margin: 0">
          试运行结果
          <span class="badge" :class="execution.state === 'succeeded' ? 'badge-success' : execution.state === 'running' ? 'badge-warning' : 'badge-danger'">
            {{ { pending: '等待', running: '执行中', succeeded: '成功', failed: '失败', canceled: '已取消' }[execution.state] || execution.state }}
          </span>
        </h3>
        <span class="muted" style="font-size: 12px">{{ formatDate(execution.updated_at) }}</span>
      </div>
      <div v-if="execution.error" class="alert alert-error mt-16">{{ execution.error }}</div>
      <div class="wf-result-grid mt-16">
        <div v-for="n in flowNodes" :key="n.id" class="wf-result-node">
          <div class="flex">
            <span class="badge" :class="{
              'badge-success': nodeState(n.id) === 'succeeded',
              'badge-danger': ['failed', 'timeout'].includes(nodeState(n.id)),
              'badge-warning': nodeState(n.id) === 'running'
            }">{{ stateLabel(nodeState(n.id)) }}</span>
            <strong>{{ n.data.node.capability || n.id }}</strong>
            <span class="muted" style="font-size: 11px">{{ n.id }}</span>
          </div>
          <pre v-if="execution.outputs?.[n.id]" class="wf-json">{{ JSON.stringify(execution.outputs[n.id], null, 2) }}</pre>
          <div v-else class="muted" style="font-size: 12px; padding: 6px 0">无输出</div>
        </div>
      </div>
    </div>

    <div v-if="jsonOpen" class="modal-mask" @click.self="jsonOpen = false">
      <div class="modal panel">
        <div class="modal-header">
          <h3 style="margin: 0">workflow.json</h3>
          <button class="modal-close" @click="jsonOpen = false">✕</button>
        </div>
        <textarea v-model="jsonText" class="textarea code" rows="18" spellcheck="false" style="font-size: 12px"></textarea>
        <div class="modal-foot">
          <span class="muted" style="font-size: 12px">可直接编辑后应用；引擎执行时忽略 position 字段</span>
          <div class="flex">
            <button class="btn" @click="jsonOpen = false">取消</button>
            <button class="btn btn-primary" @click="applyJson">应用 JSON</button>
          </div>
        </div>
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
  grid-template-columns: 210px 1fr 330px;
  min-height: 0;
}
.wf-palette {
  border-right: 1px solid var(--border);
  background: var(--panel);
  padding: 14px;
  overflow: auto;
}
.wf-panel-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
  font-size: 14px;
  margin-bottom: 12px;
}
.wf-close { background: none; border: none; color: var(--muted); cursor: pointer; font-size: 14px; }
.wf-close:hover { color: var(--text); }
.wf-palette-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px;
  margin-bottom: 8px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--panel-2);
  cursor: grab;
  transition: border-color 0.15s ease;
}
.wf-palette-item:hover { border-color: var(--primary); }
.wf-palette-icon {
  width: 34px; height: 34px; border-radius: 9px;
  display: flex; align-items: center; justify-content: center; font-size: 16px; flex-shrink: 0;
}
.wf-palette-name { font-weight: 600; font-size: 13px; }
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
.cap-item.active { background: rgba(79, 140, 255, 0.12); color: #9cc2ff; }
.cap-item:disabled { cursor: default; opacity: 0.75; }
.cap-empty { padding: 10px; font-size: 12px; }
.var-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip {
  padding: 3px 9px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--panel-2);
  color: #9cc2ff;
  font-size: 11px;
  cursor: pointer;
  font-family: monospace;
}
.chip:hover { border-color: var(--primary); }
.chip-up { color: #7ce3ab; }
.checkbox { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--muted); padding-top: 9px; }
.code { font-family: 'Cascadia Code', Consolas, monospace; font-size: 12px; }
.btn-block { width: 100%; }
.wf-result {
  margin: 14px 16px 16px;
  max-height: 300px;
  overflow: auto;
  flex-shrink: 0;
}
.wf-result-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; }
.wf-result-node { border: 1px solid var(--border); border-radius: 8px; padding: 10px; background: var(--panel-2); }
.wf-json {
  margin: 8px 0 0;
  padding: 8px;
  background: var(--bg);
  border-radius: 6px;
  border: 1px solid var(--border);
  font-size: 11px;
  overflow: auto;
  max-height: 160px;
}
.modal-mask {
  position: fixed; inset: 0; background: rgba(5, 8, 16, 0.72); z-index: 100;
  display: flex; align-items: center; justify-content: center; padding: 20px;
}
.modal { width: 720px; max-width: 100%; max-height: 90vh; overflow: auto; }
.modal-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.modal-close { background: none; border: none; color: var(--muted); font-size: 16px; cursor: pointer; }
.modal-foot { display: flex; justify-content: space-between; align-items: center; margin-top: 14px; }
</style>
