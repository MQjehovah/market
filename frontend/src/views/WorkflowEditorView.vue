<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { VueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import { api } from '../api'

const TYPE_LABELS = { tool: '工具', agent: 'Agent', skill: '技能', mcp: 'MCP' }

const capabilities = ref({ tool: [], agent: [], skill: [], mcp: [] })
const nodes = ref([])
const edges = ref([])
const selectedNode = ref(null)
const nodeCounter = ref(0)
const notice = ref('')
const error = ref('')
const created = ref(null)

const form = reactive({ name: '', version: '0.1.0', description: '' })
const addForm = reactive({ type: 'tool', capability: '', params: '{}' })
const nodeForm = reactive({ id: '', capability: '', version: '', params: '{}' })

const availableCaps = computed(() => capabilities.value[addForm.type] || [])

async function loadCatalog() {
  const [t, a, s, m] = await Promise.all([
    api.get('/capabilities?type=tool&status=published&page_size=100'),
    api.get('/capabilities?type=agent&status=published&page_size=100'),
    api.get('/capabilities?type=skill&status=published&page_size=100'),
    api.get('/capabilities?type=mcp&status=published&page_size=100')
  ])
  capabilities.value = { tool: t.items, agent: a.items, skill: s.items, mcp: m.items }
}

function addNode() {
  const cap = capabilities.value[addForm.type].find((c) => c.name === addForm.capability)
  if (!cap) return
  nodeCounter.value += 1
  const id = `n${nodeCounter.value}`
  nodes.value.push({
    id,
    type: 'default',
    position: { x: 100 + nodes.value.length * 60, y: 80 + nodes.value.length * 50 },
    data: {
      label: `${cap.name} · ${TYPE_LABELS[addForm.type]}`,
      nodeType: addForm.type,
      capability: cap.name,
      version: cap.version,
      params: addForm.params
    }
  })
  addForm.params = '{}'
}

function onConnect(params) {
  edges.value.push({ id: `e${Date.now()}`, source: params.source, target: params.target })
}

function selectNode(event) {
  selectedNode.value = event.node
  Object.assign(nodeForm, {
    id: event.node.id,
    capability: event.node.data.capability,
    version: event.node.data.version || '',
    params:
      typeof event.node.data.params === 'string'
        ? event.node.data.params
        : JSON.stringify(event.node.data.params || {}, null, 2)
  })
}

function saveNode() {
  if (!selectedNode.value) return
  const n = nodes.value.find((x) => x.id === selectedNode.value.id)
  n.data = {
    label: `${nodeForm.capability} · ${TYPE_LABELS[n.data.nodeType]}`,
    nodeType: n.data.nodeType,
    capability: nodeForm.capability,
    version: nodeForm.version,
    params: nodeForm.params
  }
  selectedNode.value = null
}

function removeNode(id) {
  nodes.value = nodes.value.filter((n) => n.id !== id)
  edges.value = edges.value.filter((e) => e.source !== id && e.target !== id)
  selectedNode.value = null
}

function buildWorkflow() {
  const wfNodes = nodes.value.map((n) => ({
    id: n.id,
    type: n.data.nodeType,
    capability: n.data.capability,
    ...(n.data.version ? { version: n.data.version } : {}),
    params: n.data.params && n.data.params.trim() ? JSON.parse(n.data.params) : {}
  }))
  return {
    name: form.name,
    description: form.description,
    version: form.version,
    on_error: 'fail',
    nodes: wfNodes,
    edges: edges.value.map((e) => ({ from: e.source, to: e.target }))
  }
}

async function save() {
  error.value = ''
  notice.value = ''
  created.value = null
  if (!form.name) {
    error.value = '请填写工作流名称'
    return
  }
  if (nodes.value.length === 0) {
    error.value = '画布上还没有节点'
    return
  }
  let workflow
  try {
    workflow = buildWorkflow()
  } catch (e) {
    error.value = `节点参数 JSON 非法：${e.message}`
    return
  }
  try {
    created.value = await api.post('/workflows', {
      name: form.name,
      description: form.description,
      version: form.version,
      category: '工作流',
      tags: ['工作流'],
      visibility: 'internal',
      workflow
    })
    notice.value = '工作流已创建（草稿），可提交审核发布'
  } catch (e) {
    error.value = e.message
  }
}

onMounted(loadCatalog)
</script>

<template>
  <div>
    <div class="panel">
      <h2>工作流设计器</h2>
      <p class="muted" style="font-size: 13px">
        从左侧添加节点（工具 / Agent / 技能 / MCP），把节点输出口拖到下一个节点输入口连线；点击节点可编辑参数。节点间用
        <code>${'${nodeId.key}'}</code> 引用输出。
      </p>
      <div v-if="error" class="alert alert-error">{{ error }}</div>
      <div v-if="notice" class="alert alert-success">{{ notice }}</div>

      <div class="grid mt-16" style="grid-template-columns: 220px 1fr">
        <div class="panel toolbox">
          <h3>添加节点</h3>
          <div class="field">
            <label>类型</label>
            <select v-model="addForm.type" class="select" @change="addForm.capability = ''">
              <option value="tool">工具</option>
              <option value="agent">Agent</option>
              <option value="skill">技能</option>
              <option value="mcp">MCP</option>
            </select>
          </div>
          <div class="field">
            <label>能力</label>
            <select v-model="addForm.capability" class="select">
              <option value="">选择…</option>
              <option v-for="c in availableCaps" :key="c.id" :value="c.name">{{ c.name }}</option>
            </select>
          </div>
          <div class="field">
            <label>默认参数 JSON</label>
            <textarea v-model="addForm.params" class="textarea" rows="5" spellcheck="false"></textarea>
          </div>
          <button class="btn btn-primary" @click="addNode">+ 添加节点</button>

          <div class="mt-24">
            <h3>工作流信息</h3>
            <div class="field"><label>名称</label><input v-model="form.name" class="input" /></div>
            <div class="field"><label>版本</label><input v-model="form.version" class="input" /></div>
            <div class="field"><label>描述</label><input v-model="form.description" class="input" /></div>
            <button class="btn btn-primary" @click="save">保存为草稿</button>
            <router-link v-if="created" class="btn mt-8" :to="`/capabilities/${created.id}`">查看</router-link>
          </div>
        </div>

        <div class="canvas-wrap">
          <VueFlow
            v-model:nodes="nodes"
            v-model:edges="edges"
            :fit-view-on-init="true"
            :min-zoom="0.2"
            :max-zoom="2"
            @connect="onConnect"
            @node-click="selectNode"
          >
            <Background />
            <Controls />
          </VueFlow>
        </div>
      </div>

      <div v-if="selectedNode" class="panel mt-16">
        <h3>编辑节点 {{ selectedNode.id }}</h3>
        <div class="grid" style="grid-template-columns: 1fr 1fr 2fr">
          <div class="field"><label>能力</label><input v-model="nodeForm.capability" class="input" /></div>
          <div class="field"><label>版本（留空=最新）</label><input v-model="nodeForm.version" class="input" /></div>
          <div class="field"><label>参数 JSON</label><input v-model="nodeForm.params" class="input" spellcheck="false" /></div>
        </div>
        <div class="flex" style="gap: 10px">
          <button class="btn btn-primary btn-sm" @click="saveNode">保存节点</button>
          <button class="btn btn-danger btn-sm" @click="removeNode(selectedNode.id)">删除节点</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.toolbox { max-height: 560px; overflow: auto; }
.canvas-wrap {
  height: 520px;
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
  background: var(--panel-2);
}
.field { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
.field label { font-size: 13px; color: var(--muted); }
.mt-8 { margin-top: 8px; }
</style>
