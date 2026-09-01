<script setup>
import { computed, ref, watch } from 'vue'
import { marked } from 'marked'
import { api } from '../api'
import { formatSize } from '../utils/format'

const props = defineProps({
  capabilityId: { type: String, required: true },
  compact: { type: Boolean, default: false }
})

const loading = ref(false)
const error = ref('')
const files = ref([])
const artifactName = ref('')
const artifactSize = ref(0)
const selectedPath = ref('')
const fileLoading = ref(false)
const fileError = ref('')
const filePayload = ref(null)

const PREFERRED = [
  'SKILL.md',
  'README.md',
  'PROMPT.md',
  'agent.json',
  'plugin.json',
  'skill.json',
  'mcp.json',
  'tool.json',
  'workflow.json',
  'implementation/tool.py'
]

/** Flat rows: dirs (non-clickable) + files with depth for indent */
const treeRows = computed(() => {
  const entries = files.value.slice().sort((a, b) => a.path.localeCompare(b.path))
  const rows = []
  const seenDirs = new Set()
  for (const entry of entries) {
    const parts = entry.path.split('/').filter(Boolean)
    let prefix = ''
    parts.forEach((part, idx) => {
      const isLeaf = idx === parts.length - 1
      if (!isLeaf) {
        prefix = prefix ? `${prefix}/${part}` : part
        if (!seenDirs.has(prefix)) {
          seenDirs.add(prefix)
          rows.push({ kind: 'dir', path: prefix, name: part, depth: idx })
        }
      } else {
        rows.push({
          kind: 'file',
          path: entry.path,
          name: part,
          depth: idx,
          text: entry.text,
          size: entry.size
        })
      }
    })
  }
  return rows
})

const previewHtml = computed(() => {
  const p = filePayload.value
  if (!p || p.binary || !p.content) return ''
  const path = (p.path || '').toLowerCase()
  if (!path.endsWith('.md') && !path.endsWith('.markdown')) return ''
  try {
    return marked.parse(p.content, { gfm: true, breaks: true })
  } catch {
    return ''
  }
})

const languageHint = computed(() => {
  const path = selectedPath.value || ''
  const name = path.split('/').pop() || path
  if (!name.includes('.')) return ''
  return name.split('.').pop()
})

function pickDefault(list) {
  for (const pref of PREFERRED) {
    const hit = list.find((f) => f.path === pref || f.path.toLowerCase() === pref.toLowerCase())
    if (hit) return hit.path
  }
  const md = list.find((f) => f.text && f.path.toLowerCase().endsWith('.md'))
  if (md) return md.path
  const text = list.find((f) => f.text)
  return text?.path || list[0]?.path || ''
}

async function loadTree() {
  if (!props.capabilityId) return
  loading.value = true
  error.value = ''
  files.value = []
  filePayload.value = null
  selectedPath.value = ''
  try {
    const data = await api.get(`/capabilities/${props.capabilityId}/package/tree`)
    files.value = data.files || []
    artifactName.value = data.artifact_filename || ''
    artifactSize.value = data.artifact_size || 0
    const first = pickDefault(files.value)
    if (first) await openFile(first)
  } catch (e) {
    error.value = e.message || '无法加载包内文件'
  } finally {
    loading.value = false
  }
}

async function openFile(path) {
  if (!path || !props.capabilityId) return
  selectedPath.value = path
  fileLoading.value = true
  fileError.value = ''
  filePayload.value = null
  try {
    filePayload.value = await api.get(
      `/capabilities/${props.capabilityId}/package/file?path=${encodeURIComponent(path)}`
    )
  } catch (e) {
    fileError.value = e.message || '无法读取文件'
  } finally {
    fileLoading.value = false
  }
}

watch(
  () => props.capabilityId,
  () => {
    loadTree()
  },
  { immediate: true }
)
</script>

<template>
  <div class="pkg-preview" :class="{ compact }">
    <div v-if="loading" class="muted state">加载包内文件…</div>
    <div v-else-if="error" class="state err">{{ error }}</div>
    <template v-else>
      <div v-if="artifactName" class="pkg-meta muted">
        {{ artifactName }}
        <span v-if="artifactSize"> · {{ formatSize(artifactSize) }}</span>
        <span> · {{ files.length }} 个文件</span>
      </div>
      <div v-if="files.length === 0" class="muted state">包内无文件</div>
      <div v-else class="pkg-split">
        <div class="pkg-tree">
          <div
            v-for="row in treeRows"
            :key="row.kind + ':' + row.path"
            class="tree-row"
            :class="[row.kind, { active: row.kind === 'file' && selectedPath === row.path }]"
            :style="{ paddingLeft: `${10 + row.depth * 14}px` }"
          >
            <span v-if="row.kind === 'dir'" class="tree-label">
              <span class="ico dir"></span>{{ row.name }}
            </span>
            <button
              v-else
              type="button"
              class="tree-file"
              @click="openFile(row.path)"
            >
              <span class="ico" :class="row.text ? 'doc' : 'bin'"></span>
              <span class="tree-name">{{ row.name }}</span>
            </button>
          </div>
        </div>
        <div class="pkg-view">
          <div class="view-head">
            <span class="view-path">{{ selectedPath || '选择文件' }}</span>
            <span v-if="filePayload" class="muted view-size">
              {{ formatSize(filePayload.size) }}
              <template v-if="filePayload.truncated"> · 已截断</template>
            </span>
          </div>
          <div v-if="fileLoading" class="muted state">读取中…</div>
          <div v-else-if="fileError" class="state err">{{ fileError }}</div>
          <div v-else-if="filePayload?.binary" class="muted state">二进制文件，暂不支持预览</div>
          <div v-else-if="previewHtml" class="view-md" v-html="previewHtml"></div>
          <pre v-else-if="filePayload?.content != null" class="view-code"><code :data-lang="languageHint">{{ filePayload.content }}</code></pre>
          <div v-else class="muted state">选择左侧文件预览</div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.pkg-preview { min-width: 0; }
.pkg-meta { font-size: 12px; margin-bottom: 8px; }
.pkg-split {
  display: grid;
  grid-template-columns: minmax(180px, 280px) 1fr;
  gap: 0;
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
  min-height: min(640px, calc(100vh - 220px));
  height: min(720px, calc(100vh - 200px));
  background: #fff;
}
.pkg-preview.compact .pkg-split {
  min-height: 280px;
  height: auto;
  max-height: 420px;
}
.pkg-tree {
  border-right: 1px solid var(--border);
  background: #fafbfc;
  overflow: auto;
  height: 100%;
  padding: 6px 0;
}
.pkg-preview.compact .pkg-tree,
.pkg-preview.compact .pkg-view {
  max-height: 420px;
  height: auto;
}
.pkg-view {
  display: flex;
  flex-direction: column;
  min-width: 0;
  height: 100%;
  overflow: hidden;
}
.tree-row.dir .tree-label {
  display: flex; align-items: center; gap: 6px;
  font-size: 12px; color: var(--muted); font-weight: 600;
  padding: 4px 8px 4px 0;
}
.tree-file {
  display: flex; align-items: center; gap: 6px;
  width: 100%; border: none; background: none; text-align: left;
  padding: 4px 8px 4px 0; cursor: pointer; font-size: 12px; color: var(--text);
}
.tree-row.file:hover,
.tree-row.file.active { background: #eef2ff; }
.tree-row.file.active .tree-file { color: var(--primary); font-weight: 600; }
.tree-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ico {
  width: 12px; height: 12px; border-radius: 2px; flex: none;
  border: 1px solid var(--border); background: #fff;
}
.ico.dir { background: #dbe4ff; border-color: #b6c7ff; }
.ico.doc { background: #e8f5ee; border-color: #b7dfc8; }
.ico.bin { background: #f1f5f9; }

.view-head {
  display: flex; justify-content: space-between; gap: 8px;
  padding: 8px 12px; border-bottom: 1px solid var(--border);
  background: #fafbfc; font-size: 12px;
}
.view-path {
  font-family: 'Cascadia Code', Consolas, monospace;
  color: var(--text); word-break: break-all;
}
.view-size { flex: none; }
.view-code {
  margin: 0; padding: 12px 14px; overflow: auto; flex: 1;
  font-size: 12px; line-height: 1.5;
  font-family: 'Cascadia Code', Consolas, monospace;
  white-space: pre-wrap; word-break: break-word;
}
.view-md {
  padding: 12px 14px; overflow: auto; flex: 1;
  font-size: 13px; line-height: 1.6;
}
.view-md :deep(h1),
.view-md :deep(h2),
.view-md :deep(h3) { margin: 10px 0 6px; font-size: 15px; }
.view-md :deep(pre) {
  background: #f4f6f9; padding: 10px; border-radius: 8px; overflow: auto; font-size: 12px;
}
.view-md :deep(code) { font-family: 'Cascadia Code', Consolas, monospace; }
.state { padding: 24px 12px; text-align: center; font-size: 13px; }
.state.err { color: var(--danger); }

@media (max-width: 720px) {
  .pkg-split {
    grid-template-columns: 1fr;
    height: auto;
    min-height: 0;
  }
  .pkg-tree { max-height: 220px; height: auto; border-right: none; border-bottom: 1px solid var(--border); }
  .pkg-view { height: min(60vh, 520px); }
}
</style>
