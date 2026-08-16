<script setup>
import { computed } from 'vue'
import { Handle, Position } from '@vue-flow/core'

const props = defineProps({
  id: { type: String, required: true },
  data: { type: Object, default: () => ({}) },
  selected: { type: Boolean, default: false }
})

const TYPE_META = {
  tool: { label: '工具', color: '#4f8cff' },
  agent: { label: 'Agent', color: '#9d6bff' },
  skill: { label: '技能', color: '#2fbf71' },
  mcp: { label: 'MCP', color: '#e2a93b' }
}

const node = computed(() => props.data?.node || {})
const meta = computed(() => TYPE_META[node.value.type] || TYPE_META.tool)
const capabilityName = computed(() => node.value.capability || '选择能力…')
const paramCount = computed(() => Object.keys(node.value.params || {}).length)
const hasVersion = computed(() => Boolean(node.value.version))
</script>

<template>
  <div class="wf-node" :class="{ selected, error: !node.capability }">
    <Handle type="target" :position="Position.Top" />
    <div class="wf-node-head" :style="{ borderColor: meta.color }">
      <span class="wf-node-dot" :style="{ background: meta.color }"></span>
      <span class="wf-node-type" :style="{ color: meta.color }">{{ meta.label }}</span>
      <span class="wf-node-id">{{ id }}</span>
    </div>
    <div class="wf-node-body" :title="capabilityName">
      <div class="wf-node-name" :class="{ placeholder: !node.capability }">{{ capabilityName }}</div>
      <div class="wf-node-meta">
        <span v-if="hasVersion">v{{ node.version }}</span>
        <span v-if="paramCount">{{ paramCount }} 个参数</span>
        <span v-if="node.retries">重试 {{ node.retries }}</span>
        <span v-if="node.timeout_seconds && node.timeout_seconds !== 120">{{ node.timeout_seconds }}s</span>
      </div>
    </div>
    <Handle type="source" :position="Position.Bottom" />
  </div>
</template>

<style scoped>
.wf-node {
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
.wf-node.error { border-color: rgba(229, 83, 75, 0.6); }
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
.wf-node-meta {
  display: flex;
  gap: 8px;
  margin-top: 4px;
  color: var(--muted);
  font-size: 11px;
  flex-wrap: wrap;
}
</style>
