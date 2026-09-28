<script setup>
/** 轻量多选(搜索 + 芯片 + 可手输新增): 供能力白名单(用户/部门)配置用。 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  // [{value,label}] 或 string[] 均可
  options: { type: Array, default: () => [] },
  placeholder: { type: String, default: '搜索并选择' },
  /** 允许手动输入下拉里不存在的值(回车/点提示项新增) */
  allowCreate: { type: Boolean, default: false }
})
const emit = defineEmits(['update:modelValue'])

const open = ref(false)
const query = ref('')
const rootEl = ref(null)

const normOptions = computed(() =>
  props.options.map((o) => (typeof o === 'string' ? { value: o, label: o } : o))
)
const selected = computed(() => props.modelValue || [])
const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  const sel = new Set(selected.value)
  return normOptions.value
    .filter((o) => !sel.has(o.value))
    .filter((o) => !q || o.label.toLowerCase().includes(q) || String(o.value).toLowerCase().includes(q))
    .slice(0, 50)
})
const canCreate = computed(() => {
  const q = query.value.trim()
  return props.allowCreate && !!q
    && !normOptions.value.some((o) => o.value === q)
    && !selected.value.includes(q)
})

function setValue(list) {
  emit('update:modelValue', list)
}
function toggle(value) {
  if (selected.value.includes(value)) {
    setValue(selected.value.filter((v) => v !== value))
  } else {
    setValue([...selected.value, value])
  }
  query.value = ''
  open.value = true
}
function addCustom() {
  if (!canCreate.value) return
  setValue([...selected.value, query.value.trim()])
  query.value = ''
}
function labelOf(value) {
  const hit = normOptions.value.find((o) => o.value === value)
  return hit ? hit.label : value
}
function onEnter() {
  if (canCreate.value) {
    addCustom()
    return
  }
  const first = filtered.value[0]
  if (first) toggle(first.value)
}
function onBackspace() {
  if (query.value === '' && selected.value.length) {
    setValue(selected.value.slice(0, -1))
  }
}
function onDocMouseDown(e) {
  if (rootEl.value && !rootEl.value.contains(e.target)) open.value = false
}
onMounted(() => document.addEventListener('mousedown', onDocMouseDown, true))
onBeforeUnmount(() => document.removeEventListener('mousedown', onDocMouseDown, true))
</script>

<template>
  <div ref="rootEl" class="ms">
    <div class="ms-box" :class="{ open }" @click="open = true">
      <span v-for="v in selected" :key="v" class="ms-chip">
        {{ labelOf(v) }}
        <button type="button" class="ms-x" @click.stop="toggle(v)">✕</button>
      </span>
      <input
        v-model="query"
        class="ms-input"
        :placeholder="selected.length ? '' : placeholder"
        @focus="open = true"
        @keydown.enter.prevent="onEnter"
        @keydown.backspace="onBackspace"
      />
    </div>
    <div v-if="open" class="ms-menu">
      <button
        v-for="o in filtered"
        :key="o.value"
        type="button"
        class="ms-item"
        @click="toggle(o.value)"
      >
        <span>{{ o.label }}</span>
        <span v-if="o.value !== o.label" class="ms-sub">{{ o.value }}</span>
      </button>
      <button v-if="canCreate" type="button" class="ms-item create" @click="addCustom">
        ＋ 添加「{{ query.trim() }}」
      </button>
      <div v-if="!filtered.length && !canCreate" class="ms-empty">无匹配，可直接输入后回车</div>
    </div>
  </div>
</template>

<style scoped>
.ms { position: relative; }
.ms-box {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  min-height: 38px;
  padding: 5px 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--panel);
  cursor: text;
}
.ms-box.open { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(47, 107, 255, 0.12); }
.ms-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 6px 2px 8px;
  border-radius: 6px;
  background: var(--bg-soft);
  color: var(--text);
  font-size: 12.5px;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ms-x { border: none; background: transparent; color: var(--muted); cursor: pointer; font-size: 11px; padding: 0 2px; }
.ms-x:hover { color: var(--text); }
.ms-input {
  flex: 1;
  min-width: 120px;
  border: none;
  outline: none;
  background: transparent;
  color: var(--text);
  font-size: 13px;
  padding: 2px 0;
}
.ms-menu {
  position: absolute;
  z-index: 40;
  left: 0;
  right: 0;
  top: calc(100% + 4px);
  max-height: 240px;
  overflow: auto;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: 0 12px 30px rgba(16, 24, 40, 0.12);
  padding: 4px;
}
.ms-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  width: 100%;
  text-align: left;
  border: none;
  background: transparent;
  color: var(--text);
  font-size: 13px;
  padding: 7px 9px;
  border-radius: 7px;
  cursor: pointer;
}
.ms-item:hover { background: var(--bg-soft); }
.ms-sub { color: var(--muted); font-size: 11.5px; }
.ms-item.create { color: var(--primary); }
.ms-empty { color: var(--muted); font-size: 12.5px; padding: 8px 9px; }
</style>
