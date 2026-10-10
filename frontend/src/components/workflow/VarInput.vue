<script setup>
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'


const props = defineProps({
  modelValue: { type: String, default: '' },
  multiline: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  placeholder: { type: String, default: '' },
  variables: { type: Array, default: () => [] },
  noVars: { type: Boolean, default: false }
})
const emit = defineEmits(['update:modelValue'])

const el = ref(null)
const open = ref(false)
const menuStyle = ref({})
const MENU_WIDTH = 290
const MENU_MAX_H = 320

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
  if (!e.target.closest('.var-input') && !e.target.closest('.var-menu')) close()
}

function positionMenu() {
  const node = el.value
  if (!node) return
  const r = node.getBoundingClientRect()
  const left = Math.max(8, Math.min(r.right - MENU_WIDTH, window.innerWidth - MENU_WIDTH - 8))
  const top = Math.min(r.bottom + 6, window.innerHeight - MENU_MAX_H - 8)
  menuStyle.value = {
    position: 'fixed',
    top: `${Math.max(8, top)}px`,
    left: `${left}px`,
    width: `${MENU_WIDTH}px`
  }
}

function onScrollResize() {
  if (open.value) positionMenu()
}

function close() {
  open.value = false
  document.removeEventListener('click', onDocClick, true)
  window.removeEventListener('scroll', onScrollResize, true)
  window.removeEventListener('resize', onScrollResize)
}

function toggle() {
  if (props.disabled) return
  if (open.value) {
    close()
    return
  }
  open.value = true
  nextTick(positionMenu)
  document.addEventListener('click', onDocClick, true)
  window.addEventListener('scroll', onScrollResize, true)
  window.addEventListener('resize', onScrollResize)
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

onBeforeUnmount(() => close())
</script>

<template>
  <div class="var-input" :class="{ 'no-vars': noVars, ml: multiline }">
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
    <button v-if="!noVars" type="button" class="var-btn" :disabled="disabled" title="插入变量" @click="toggle">{x}</button>

    <div v-if="open" class="var-menu" :style="menuStyle">
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
.var-input { position: relative; display: block; width: 100%; }
.var-field { width: 100%; }
.var-input:not(.no-vars) .var-field { padding-right: 34px; }
.var-btn {
  position: absolute;
  right: 3px;
  top: 50%;
  transform: translateY(-50%);
  border: none;
  background: transparent;
  border-radius: 5px;
  padding: 2px 6px;
  font-size: 11px;
  font-family: 'Cascadia Code', Consolas, monospace;
  cursor: pointer;
  color: var(--muted);
  line-height: 1.5;
}
.var-input.ml .var-btn { top: 7px; transform: none; }
.var-btn:hover { background: var(--panel-2); color: var(--primary); }
.var-menu {
  z-index: 200;
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
