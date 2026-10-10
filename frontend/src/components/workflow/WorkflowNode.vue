<script setup>
import { computed, inject } from 'vue'
import { Handle, Position } from '@vue-flow/core'

const props = defineProps({
  id: { type: String, required: true },
  data: { type: Object, default: () => ({}) },
  selected: { type: Boolean, default: false }
})

const runState = inject('wf-run-state', null)
const canEditState = inject('wf-can-edit', null)
const nodeActions = inject('wf-node-actions', null)
const canEdit = computed(() => canEditState?.value ?? false)

const STATUS_META = {
  succeeded: { icon: '✓', kind: 'ok', label: '成功' },
  failed: { icon: '✕', kind: 'bad', label: '失败' },
  timeout: { icon: '⏱', kind: 'bad', label: '超时' },
  running: { icon: '⟳', kind: 'run', label: '执行中' },
  waiting: { icon: '❚❚', kind: 'warn', label: '待审批' },
  skipped: { icon: '—', kind: 'idle', label: '已跳过' },
  pending: { icon: '○', kind: 'idle', label: '等待' }
}
const status = computed(() => runState?.value?.node_states?.[props.id] || '')
const statusMeta = computed(() => STATUS_META[status.value] || null)
const hasOutput = computed(() => {
  const ex = runState?.value
  if (!ex) return false
  const out = ex.node_outputs?.[props.id]
  return (out !== undefined ? out : ex.outputs?.[props.id]) !== undefined
})

function viewOutput() {
  if (nodeActions?.viewOutput) nodeActions.viewOutput(props.id)
}

function removeNode() {
  if (nodeActions?.remove) nodeActions.remove(props.id)
}

const TYPE_META = {
  start: { label: '开始', color: '#2fbf71' },
  end: { label: '结束', color: '#e5534b' },
  answer: { label: '回复', color: '#2fbf71' },
  llm: { label: 'LLM', color: '#4f8cff' },
  agent: { label: 'Agent', color: '#9d6bff' },
  'parameter-extractor': { label: '参数提取', color: '#9d6bff' },
  'knowledge-retrieval': { label: '知识检索', color: '#2fbf71' },
  'if-else': { label: '条件分支', color: '#e2a93b' },
  'question-classifier': { label: '问题分类', color: '#e2a93b' },
  iteration: { label: '迭代', color: '#e2a93b' },
  loop: { label: '循环', color: '#e2a93b' },
  'variable-aggregator': { label: '变量聚合', color: '#e2a93b' },
  'variable-assigner': { label: '变量赋值', color: '#e2a93b' },
  approval: { label: '人工审批', color: '#e2a93b' },
  code: { label: '代码', color: '#4f8cff' },
  'http-request': { label: 'HTTP', color: '#4f8cff' },
  'template-transform': { label: '模板', color: '#4f8cff' },
  'doc-extractor': { label: '文档抽取', color: '#4f8cff' },
  'list-operator': { label: '列表操作', color: '#4f8cff' },
  tool: { label: '工具', color: '#4f8cff' },
  skill: { label: '技能', color: '#2fbf71' },
  mcp: { label: '连接器', color: '#e2a93b' },
  workflow: { label: '工作流', color: '#0ea5e9' }
}

const node = computed(() => props.data?.node || {})
const meta = computed(() => TYPE_META[node.value.type] || { label: node.value.type, color: '#4f8cff' })
const isStart = computed(() => node.value.type === 'start')
const isEnd = computed(() => node.value.type === 'end')
const isMarket = computed(() => ['tool', 'agent', 'skill', 'mcp', 'workflow'].includes(node.value.type))
const valid = computed(() => (isMarket.value ? Boolean(node.value.capability) : true))

const subtitle = computed(() => {
  const n = node.value
  const p = n.params || {}
  if (isMarket.value) return n.capability || '选择能力…'
  if (n.type === 'llm') return (p.prompt || '').slice(0, 48) || 'LLM 调用'
  if (n.type === 'http-request') return `${p.method || 'GET'} ${p.url || ''}`.trim() || 'HTTP 请求'
  if (n.type === 'knowledge-retrieval') return '知识库检索'
  if (n.type === 'if-else') return 'true / false 分流'
  if (n.type === 'question-classifier') return `${(p.classes || []).length} 个类别`
  if (n.type === 'approval') return p.title || '人工审批'
  if (n.type === 'code') return String(p.language || 'python')
  if (n.type === 'mcp') return p.op === 'call' ? `调用 ${p.tool || '…'}` : (n.capability || '连接器')
  return meta.value.label
})

const branchHandles = computed(() => {
  const t = node.value.type
  if (t === 'if-else' || t === 'if_else') {
    return [
      { id: 'true', label: 'true' },
      { id: 'false', label: 'false' }
    ]
  }
  if (t === 'question-classifier' || t === 'question_classifier') {
    const cls = node.value.params?.classes
    return Array.isArray(cls) ? cls.map((c) => ({ id: String(c.id), label: c.name || String(c.id) })) : []
  }
  if (t === 'approval' || t === 'human-approval') {
    return [
      { id: 'true', label: '通过' },
      { id: 'false', label: '驳回' }
    ]
  }
  return null
})
</script>

<template>
  <div
    class="wf-node"
    :class="[{ selected, invalid: !valid }, statusMeta ? 'st-' + statusMeta.kind : '']"
  >
    <div class="wf-node-tools" @mousedown.stop @click.stop>
      <button
        v-if="runState"
        class="wf-tool-btn"
        type="button"
        :title="hasOutput ? '查看运行输出' : '本次运行暂无输出'"
        @click="viewOutput"
      >▤ 输出</button>
      <button
        v-if="canEdit"
        class="wf-tool-btn danger"
        type="button"
        title="删除节点"
        @click="removeNode"
      >✕</button>
    </div>
    <Handle v-if="!isStart" type="target" :position="Position.Top" />
    <div class="wf-node-head" :style="{ borderColor: meta.color }">
      <span class="wf-node-dot" :style="{ background: meta.color }"></span>
      <span class="wf-node-type" :style="{ color: meta.color }">{{ meta.label }}</span>
      <span class="wf-node-id">{{ id }}</span>
      <span
        v-if="statusMeta"
        class="wf-node-st"
        :class="['k-' + statusMeta.kind, { spin: status === 'running' }]"
        :title="statusMeta.label"
      >{{ statusMeta.icon }}</span>
    </div>
    <div class="wf-node-body">
      <div class="wf-node-name" :class="{ placeholder: !valid }">{{ subtitle }}</div>
    </div>
    <template v-if="!isEnd">
      <template v-if="branchHandles && branchHandles.length">
        <Handle
          v-for="(h, i) in branchHandles"
          :id="h.id"
          :key="h.id"
          type="source"
          :position="Position.Bottom"
          :style="{ left: `${((i + 1) / (branchHandles.length + 1)) * 100}%` }"
        />
        <div class="wf-branch-labels">
          <span
            v-for="(h, i) in branchHandles"
            :key="h.id"
            :style="{ left: `${((i + 1) / (branchHandles.length + 1)) * 100}%` }"
          >{{ h.label }}</span>
        </div>
      </template>
      <Handle v-else type="source" :position="Position.Bottom" />
    </template>
  </div>
</template>

<style scoped>
.wf-node {
  position: relative;
  width: 190px;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.28);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
  font-size: 13px;
}
.wf-node.selected {
  border-color: var(--primary);
  box-shadow: 0 0 0 2px rgba(79, 140, 255, 0.25), 0 6px 18px rgba(0, 0, 0, 0.35);
}
.wf-node.invalid { border-color: rgba(229, 83, 75, 0.6); }
.wf-node.st-ok { border-color: rgba(22, 163, 74, 0.8); box-shadow: 0 0 0 2px rgba(22, 163, 74, 0.18), 0 4px 14px rgba(0, 0, 0, 0.28); }
.wf-node.st-bad { border-color: rgba(220, 38, 38, 0.8); box-shadow: 0 0 0 2px rgba(220, 38, 38, 0.18), 0 4px 14px rgba(0, 0, 0, 0.28); }
.wf-node.st-warn { border-color: rgba(217, 119, 6, 0.75); box-shadow: 0 0 0 2px rgba(217, 119, 6, 0.16), 0 4px 14px rgba(0, 0, 0, 0.28); }
.wf-node.st-run { border-color: rgba(22, 163, 74, 0.85); animation: wfnode-pulse 1.4s ease-in-out infinite; }
@keyframes wfnode-pulse {
  0%, 100% { box-shadow: 0 0 0 2px rgba(22, 163, 74, 0.15), 0 4px 14px rgba(0, 0, 0, 0.28); }
  50% { box-shadow: 0 0 0 6px rgba(22, 163, 74, 0.12), 0 4px 14px rgba(0, 0, 0, 0.28); }
}
.wf-node-tools {
  position: absolute;
  top: -34px;
  right: 0;
  display: flex;
  gap: 4px;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.12s ease;
  z-index: 6;
}
.wf-node:hover .wf-node-tools,
.wf-node.selected .wf-node-tools { opacity: 1; pointer-events: auto; }
.wf-tool-btn {
  border: 1px solid var(--border);
  background: var(--panel);
  color: inherit;
  border-radius: 6px;
  padding: 3px 8px;
  font-size: 11px;
  line-height: 1.5;
  cursor: pointer;
  white-space: nowrap;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.25);
}
.wf-tool-btn:hover { border-color: var(--primary); color: var(--primary); }
.wf-tool-btn.danger:hover { border-color: #dc2626; color: #dc2626; }
.wf-node-st {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  line-height: 1;
  color: #fff;
  flex: none;
}
.wf-node-st.k-ok { background: #16a34a; }
.wf-node-st.k-run { background: #16a34a; }
.wf-node-st.k-bad { background: #dc2626; }
.wf-node-st.k-warn { background: #d97706; }
.wf-node-st.k-idle { background: var(--muted); }
.wf-node-st.spin { animation: wfnode-spin 1s linear infinite; }
@keyframes wfnode-spin { to { transform: rotate(360deg); } }
.wf-node-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 10px;
  border-bottom: 1px solid var(--border);
  background: var(--panel-2);
  border-radius: 9px 9px 0 0;
}
.wf-node-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.wf-node-type { font-size: 12px; font-weight: 600; }
.wf-node-id { margin-left: auto; color: var(--muted); font-size: 11px; font-family: monospace; }
.wf-node-body { padding: 8px 10px 10px; }
.wf-node-name {
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.wf-node-name.placeholder { color: var(--muted); font-weight: 400; }
.wf-branch-labels {
  position: absolute;
  left: 0;
  right: 0;
  bottom: -16px;
  height: 12px;
  pointer-events: none;
}
.wf-branch-labels span {
  position: absolute;
  transform: translateX(-50%);
  font-size: 10px;
  color: var(--muted);
}
</style>
