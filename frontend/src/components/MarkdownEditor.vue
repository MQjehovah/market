<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { marked } from 'marked'
import CodeEditor from './CodeEditor.vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  height: { type: [String, Number], default: 'min(72vh, 860px)' },
  readonly: { type: Boolean, default: false }
})

const emit = defineEmits(['update:modelValue'])

const mode = ref('split')
const codeRef = ref(null)

const previewHtml = computed(() => {
  try {
    return marked.parse(props.modelValue || '', { gfm: true, breaks: true })
  } catch {
    return '<p class="md-error">Markdown 解析失败</p>'
  }
})

function setMode(next) {
  mode.value = next
}

function onInput(val) {
  emit('update:modelValue', val)
}

watch(mode, async () => {
  await nextTick()
  codeRef.value?.layout?.()
})
</script>

<template>
  <div class="md-editor" :class="[`mode-${mode}`]">
    <div class="md-toolbar">
      <button type="button" class="md-tab" :class="{ active: mode === 'edit' }" @click="setMode('edit')">
        编辑
      </button>
      <button type="button" class="md-tab" :class="{ active: mode === 'split' }" @click="setMode('split')">
        分屏
      </button>
      <button type="button" class="md-tab" :class="{ active: mode === 'preview' }" @click="setMode('preview')">
        预览
      </button>
    </div>
    <div class="md-body">
      <div v-show="mode !== 'preview'" class="md-pane md-code">
        <CodeEditor
          ref="codeRef"
          :model-value="modelValue"
          language="markdown"
          :height="height"
          :readonly="readonly"
          @update:model-value="onInput"
        />
      </div>
      <div v-show="mode !== 'edit'" class="md-pane md-preview" :style="{ minHeight: typeof height === 'number' ? `${height + 28}px` : height }">
        <div v-if="previewHtml" class="md-html" v-html="previewHtml"></div>
        <div v-else class="muted empty">暂无内容</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.md-editor {
  min-width: 0;
  border: 1px solid var(--border-strong);
  border-radius: 10px;
  overflow: hidden;
  background: #fff;
}
.md-toolbar {
  display: flex;
  gap: 4px;
  padding: 6px 8px;
  background: var(--panel-2);
  border-bottom: 1px solid var(--border);
}
.md-tab {
  border: 1px solid transparent;
  background: transparent;
  color: var(--muted);
  border-radius: 8px;
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
}
.md-tab.active {
  color: var(--primary);
  background: var(--primary-soft);
  border-color: #c9d8ff;
}
.md-body {
  display: grid;
  min-width: 0;
}
.md-editor.mode-split .md-body {
  grid-template-columns: 1fr 1fr;
}
.md-editor.mode-edit .md-body,
.md-editor.mode-preview .md-body {
  grid-template-columns: 1fr;
}
.md-pane { min-width: 0; }
.md-editor.mode-split .md-code :deep(.code-editor-shell) {
  border: none;
  border-radius: 0;
  border-right: 1px solid var(--border);
}
.md-editor.mode-edit .md-code :deep(.code-editor-shell) {
  border: none;
  border-radius: 0;
}
.md-preview {
  overflow: auto;
  background: #fff;
}
.md-html {
  padding: 14px 16px;
  font-size: 13px;
  line-height: 1.65;
}
.md-html :deep(h1),
.md-html :deep(h2),
.md-html :deep(h3) { margin: 12px 0 8px; }
.md-html :deep(h1) { font-size: 20px; }
.md-html :deep(h2) { font-size: 17px; }
.md-html :deep(h3) { font-size: 15px; }
.md-html :deep(p) { margin: 0 0 10px; }
.md-html :deep(pre) {
  background: #f4f6f9;
  padding: 10px;
  border-radius: 8px;
  overflow: auto;
  font-size: 12px;
}
.md-html :deep(code) {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
}
.md-html :deep(ul),
.md-html :deep(ol) { padding-left: 1.4em; margin: 0 0 10px; }
.md-html :deep(blockquote) {
  margin: 0 0 10px;
  padding: 4px 12px;
  border-left: 3px solid var(--border-strong);
  color: var(--muted);
}
.md-html :deep(a) { color: var(--primary); }
.md-html :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 0 0 10px;
  font-size: 12px;
}
.md-html :deep(th),
.md-html :deep(td) {
  border: 1px solid var(--border);
  padding: 6px 8px;
  text-align: left;
}
.empty { padding: 24px 12px; text-align: center; font-size: 13px; }

@media (max-width: 800px) {
  .md-editor.mode-split .md-body {
    grid-template-columns: 1fr;
  }
  .md-editor.mode-split .md-preview {
    border-top: 1px solid var(--border);
    max-height: 280px;
  }
  .md-editor.mode-split .md-code :deep(.code-editor-shell) {
    border-right: none;
  }
}
</style>
