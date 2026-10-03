<script setup>
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  multiline: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  placeholder: { type: String, default: '' },
  variables: { type: Array, default: () => [] }
})
const emit = defineEmits(['update:modelValue'])

const el = ref(null)
const open = ref(false)

const grouped = computed(() => {
  const map = new Map()
  for (const v of props.variables) {
    const g = v.group || '变量'
    if (!map.has(g)) map.set(g, [])
    map.get(g).push(v)
  }
  return Array.from(map, ([label, items]) => ({ label, items }))
})

function onInput(e) {
  emit('update:modelValue', e.target.value)
}

function onDocClick(e) {
  if (!e.target.closest('.var-input')) close()
}

function close() {
  open.value = false
  document.removeEventListener('click', onDocClick, true)
}

function toggle() {
  if (props.disabled) return
  if (open.value) {
    close()
    return
  }
  open.value = true
  document.addEventListener('click', onDocClick, true)
}

function pick(value) {
  const node = el.value
  const cur = props.modelValue || ''
  const start = node?.selectionStart ?? cur.length
  const end = node?.selectionEnd ?? cur.length
  emit('update:modelValue', cur.slice(0, start) + value + cur.slice(end))
  nextTick(() => {
    if (node) {
      node.focus()
      const pos = start + value.length
      node.setSelectionRange(pos, pos)
    }
  })
  close()
}

onBeforeUnmount(() => document.removeEventListener('click', onDocClick, true))
</script>

<template>
  <div class="var-input">
    <textarea
      v-if="multiline"
      ref="el"
      class="textarea var-field"
      :value="modelValue"
      :placeholder="placeholder"
      :disabled="disabled"
      rows="3"
      @input="onInput"
    ></textarea>
    <input
      v-else
      ref="el"
      class="input var-field"
      :value="modelValue"
      :placeholder="placeholder"
      :disabled="disabled"
      @input="onInput"
    />
    <button type="button" class="var-btn" :disabled="disabled" title="插入变量" @click="toggle">{x}</button>

    <div v-if="open" class="var-menu">
      <div v-for="g in grouped" :key="g.label" class="var-group">
        <div class="var-group-title">{{ g.label }}</div>
        <button
          v-for="v in g.items"
          :key="v.value"
          type="button"
          class="var-item"
          @click="pick(v.value)"
        >
          <span class="var-item-label">{{ v.label }}</span>
          <code>{{ v.value }}</code>
        </button>
      </div>
      <div v-if="!grouped.length" class="var-empty">暂无可插入变量（先设开始节点入参或保留上游节点）</div>
    </div>
  </div>
</template>

<style scoped>
.var-input { position: relative; display: flex; gap: 6px; align-items: flex-start; width: 100%; }
.var-field { flex: 1; min-width: 0; }
.var-btn {
  flex: none;
  border: 1px solid var(--border);
  background: var(--panel-2);
  border-radius: 6px;
  padding: 5px 8px;
  font-size: 11px;
  font-family: 'Cascadia Code', Consolas, monospace;
  cursor: pointer;
  color: var(--muted);
  line-height: 1.2;
}
.var-btn:hover { border-color: var(--primary); color: var(--primary); }
.var-menu {
  position: absolute;
  right: 0;
  top: calc(100% + 4px);
  z-index: 60;
  width: 290px;
  max-height: 320px;
  overflow: auto;
  background: var(--panel, #fff);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: 0 10px 28px rgba(15, 23, 42, 0.18);
  padding: 6px;
}
.var-group-title { font-size: 11px; color: var(--muted); padding: 5px 8px 2px; }
.var-item {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  border: none;
  background: transparent;
  text-align: left;
  padding: 5px 8px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
  color: inherit;
}
.var-item:hover { background: var(--panel-2); }
.var-item code { color: #2451c7; font-size: 11px; font-family: monospace; white-space: nowrap; }
.var-empty { font-size: 12px; color: var(--muted); padding: 8px; line-height: 1.5; }
</style>
