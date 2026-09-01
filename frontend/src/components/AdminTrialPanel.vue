<script setup>
/**
 * 管理台「云端试用」弹出面板：从 AdminView 拆出，减轻主文件体积。
 */
import { TYPE_LABELS } from '../utils/format'

defineProps({
  show: { type: Boolean, default: false },
  debugType: { type: String, required: true },
  debugName: { type: String, required: true },
  debugCaps: { type: Array, default: () => [] }
})
const emit = defineEmits(['update:show', 'update:debugType', 'update:debugName', 'open', 'dismiss'])

function onType(e) {
  emit('update:debugType', e.target.value)
}
function onName(e) {
  emit('update:debugName', e.target.value)
}
</script>

<template>
  <div class="trial-wrap">
    <button
      class="btn"
      type="button"
      :class="{ 'btn-primary': show }"
      @click="emit('update:show', !show)"
    >
      云端试用
    </button>
    <div v-if="show" class="trial-pop panel">
      <div class="muted" style="font-size: 12px; margin-bottom: 10px">
        试用已发布资产（会计入用量）。生产请走 cap install / 本地引擎 / MCP。
      </div>
      <select class="select" :value="debugType" @change="onType">
        <option v-for="t in ['tool', 'agent', 'skill', 'mcp', 'workflow', 'plugin']" :key="t" :value="t">
          {{ TYPE_LABELS[t] }}
        </option>
      </select>
      <select class="select" style="margin-top: 8px" :value="debugName" @change="onName">
        <option value="">选择已发布的能力…</option>
        <option v-for="c in debugCaps" :key="c.id" :value="c.name">{{ c.name }}（v{{ c.version }}）</option>
      </select>
      <button class="btn btn-primary btn-block mt-12" :disabled="!debugName" @click="emit('open')">打开试用</button>
    </div>
  </div>
  <div v-if="show" class="trial-dismiss" @click="emit('dismiss')"></div>
</template>

<style scoped>
.trial-wrap { position: relative; z-index: 30; }
.trial-pop {
  position: absolute; right: 0; top: calc(100% + 8px); width: 320px; z-index: 30;
  padding: 14px; box-shadow: var(--shadow-lg);
}
.trial-dismiss { position: fixed; inset: 0; z-index: 20; }
.mt-12 { margin-top: 12px; }
</style>
