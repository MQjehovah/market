<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

let instanceSeq = 0

const props = defineProps({
  modelValue: { type: String, default: '' },
  language: { type: String, default: 'plaintext' },
  height: { type: [String, Number], default: 'min(56vh, 680px)' },
  compact: { type: Boolean, default: false },
  readonly: { type: Boolean, default: false }
})

const emit = defineEmits(['update:modelValue'])

const host = ref(null)
const ready = ref(false)
const wordWrap = ref(false)
const cursor = ref({ lineNumber: 1, column: 1 })

let monaco = null
let editor = null
let model = null
let suppress = false
let disposed = false

const tabSize = computed(() => (props.language === 'python' ? 4 : 2))
const heightCss = computed(() =>
  typeof props.height === 'number' ? `${props.height}px` : props.height
)
const langLabel = computed(() => {
  const map = { python: 'Python', json: 'JSON', markdown: 'Markdown', plaintext: 'Plain Text' }
  return map[props.language] || props.language
})
const extForLang = computed(() => {
  if (props.language === 'python') return 'py'
  if (props.language === 'json') return 'json'
  if (props.language === 'markdown') return 'md'
  return 'txt'
})

function applyOptions() {
  if (!editor) return
  editor.updateOptions({
    readOnly: props.readonly,
    minimap: { enabled: !props.compact },
    stickyScroll: { enabled: !props.compact }
  })
}

onMounted(async () => {
  const mod = await import('../monaco.js')
  if (disposed || !host.value) return
  monaco = mod.default
  const uri = monaco.Uri.parse(`inmemory://market-editor/${++instanceSeq}.${extForLang.value}`)
  model = monaco.editor.createModel(props.modelValue ?? '', props.language, uri)
  model.updateOptions({ tabSize: tabSize.value, insertSpaces: true })
  editor = monaco.editor.create(host.value, {
    model,
    theme: 'vs',
    automaticLayout: true,
    minimap: { enabled: !props.compact },
    lineNumbers: 'on',
    folding: true,
    matchBrackets: 'always',
    bracketPairColorization: { enabled: true },
    guides: { indentation: true, bracketPairs: true },
    renderLineHighlight: 'all',
    renderWhitespace: 'selection',
    stickyScroll: { enabled: !props.compact },
    smoothScrolling: true,
    mouseWheelZoom: true,
    scrollBeyondLastLine: false,
    wordWrap: wordWrap.value ? 'on' : 'off',
    tabSize: tabSize.value,
    insertSpaces: true,
    fontSize: 13,
    lineHeight: 20,
    fontFamily: "'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace",
    padding: { top: 8, bottom: 8 },
    scrollbar: { verticalScrollbarSize: 10, horizontalScrollbarSize: 10 },
    overviewRulerLanes: 3,
    glyphMargin: false,
    contextmenu: true,
    formatOnPaste: props.language === 'json',
    quickSuggestions: props.language === 'json',
    readOnly: props.readonly,
    wrappingIndent: 'same'
  })
  editor.onDidChangeModelContent(() => {
    if (suppress) return
    emit('update:modelValue', editor.getValue())
  })
  editor.onDidChangeCursorPosition((e) => {
    cursor.value = { lineNumber: e.position.lineNumber, column: e.position.column }
  })
  ready.value = true
})

watch(
  () => props.modelValue,
  (v) => {
    if (!editor) return
    const next = v ?? ''
    if (editor.getValue() === next) return
    suppress = true
    const pos = editor.getPosition()
    editor.setValue(next)
    if (pos) editor.setPosition(pos)
    suppress = false
  }
)

watch(
  () => props.language,
  (lang) => {
    if (!model || !monaco) return
    monaco.editor.setModelLanguage(model, lang)
    model.updateOptions({ tabSize: tabSize.value, insertSpaces: true })
    editor?.updateOptions({
      formatOnPaste: lang === 'json',
      quickSuggestions: lang === 'json',
      tabSize: tabSize.value
    })
  }
)

watch(() => props.readonly, applyOptions)
watch(() => props.compact, applyOptions)

watch(tabSize, (n) => {
  model?.updateOptions({ tabSize: n, insertSpaces: true })
  editor?.updateOptions({ tabSize: n })
})

function toggleWrap() {
  wordWrap.value = !wordWrap.value
  editor?.updateOptions({ wordWrap: wordWrap.value ? 'on' : 'off' })
}

async function formatDoc() {
  if (!editor || props.readonly || props.language !== 'json') return
  await editor.getAction('editor.action.formatDocument')?.run()
}

function insertAtCursor(text) {
  if (!editor || props.readonly) return
  const sel = editor.getSelection()
  editor.executeEdits('insert-at-cursor', [
    { range: sel, text: String(text ?? ''), forceMoveMarkers: true }
  ])
  editor.focus()
}

function focus() {
  editor?.focus()
}

function layout() {
  editor?.layout()
}

defineExpose({ insertAtCursor, formatDoc, focus, layout })

onBeforeUnmount(() => {
  disposed = true
  editor?.dispose()
  model?.dispose()
  editor = null
  model = null
})
</script>

<template>
  <div class="code-editor-shell" :class="{ compact }">
    <div class="code-editor-frame" :style="{ height: heightCss }">
      <div v-if="!ready" class="code-editor-loading">加载编辑器…</div>
      <div ref="host" class="code-editor-host"></div>
    </div>
    <div class="code-editor-status">
      <span>Ln {{ cursor.lineNumber }}, Col {{ cursor.column }}</span>
      <span class="sep">·</span>
      <span>{{ langLabel }}</span>
      <span class="sep">·</span>
      <span>Spaces: {{ tabSize }}</span>
      <span class="grow"></span>
      <button type="button" class="status-btn" @click="toggleWrap">
        {{ wordWrap ? '关闭换行' : '自动换行' }}
      </button>
      <button
        v-if="language === 'json' && !readonly"
        type="button"
        class="status-btn"
        @click="formatDoc"
      >
        格式化
      </button>
    </div>
  </div>
</template>

<style scoped>
.code-editor-shell {
  border: 1px solid var(--border-strong);
  border-radius: 10px;
  overflow: hidden;
  background: #fff;
  min-width: 0;
}
.code-editor-frame {
  width: 100%;
  position: relative;
}
.code-editor-host {
  width: 100%;
  height: 100%;
}
.code-editor-loading {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: var(--muted);
  background: #fff;
}
.code-editor-status {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  font-size: 11px;
  line-height: 1.4;
  color: var(--muted);
  background: var(--panel-2);
  border-top: 1px solid var(--border);
  user-select: none;
}
.code-editor-status .sep { opacity: 0.5; }
.code-editor-status .grow { flex: 1; }
.status-btn {
  border: none;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 6px;
}
.status-btn:hover {
  color: var(--primary);
  background: var(--primary-soft);
}
.code-editor-shell.compact .code-editor-status { padding: 2px 8px; }
</style>
