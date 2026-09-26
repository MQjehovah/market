<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../api'
import { formatSize } from '../utils/format'
import CodeEditor from './CodeEditor.vue'

const props = defineProps({
  capabilityId: { type: String, required: true },
  canEdit: { type: Boolean, default: false }
})
const emit = defineEmits(['saved'])

const loading = ref(false)
const error = ref('')
const notice = ref('')
const saving = ref(false)
const entries = ref([])
const deleted = ref([])
const activePath = ref('')
const newPath = ref('')
const uploadPath = ref('')
const uploadInput = ref(null)
const artifactName = ref('')
const artifactSize = ref(0)

const visibleEntries = computed(() => entries.value.filter((e) => !e.deleted))

const treeRows = computed(() => {
  const list = visibleEntries.value.slice().sort((a, b) => a.path.localeCompare(b.path))
  const rows = []
  const seenDirs = new Set()
  for (const entry of list) {
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
        rows.push({ kind: 'file', path: entry.path, name: part, depth: idx, text: entry.text })
      }
    })
  }
  return rows
})

const activeEntry = computed(() => visibleEntries.value.find((e) => e.path === activePath.value) || null)

function languageFor(path) {
  const p = (path || '').toLowerCase()
  if (p.endsWith('.json')) return 'json'
  if (p.endsWith('.py')) return 'python'
  if (p.endsWith('.md') || p.endsWith('.mdc') || p.endsWith('.markdown')) return 'markdown'
  return 'plaintext'
}

function pickDefault(list) {
  for (const pref of ['plugin.json', 'README.md', 'SKILL.md']) {
    const hit = list.find((f) => f.path.toLowerCase() === pref.toLowerCase())
    if (hit) return hit.path
  }
  const first = list.find((f) => f.text) || list[0]
  return first?.path || ''
}

async function load() {
  if (!props.capabilityId) return
  loading.value = true
  error.value = ''
  notice.value = ''
  entries.value = []
  deleted.value = []
  activePath.value = ''
  try {
    const data = await api.get(`/capabilities/${props.capabilityId}/package/tree`)
    artifactName.value = data.artifact_filename || ''
    artifactSize.value = data.artifact_size || 0
    const rows = data.files || []
    const loaded = await Promise.all(
      rows.map(async (row) => {
        const base = { path: row.path, size: row.size, text: Boolean(row.text), content: '', truncated: false, isNew: false, deleted: false }
        if (!row.text) return base
        try {
          const payload = await api.get(
            `/capabilities/${props.capabilityId}/package/file?path=${encodeURIComponent(row.path)}`
          )
          return {
            ...base,
            text: !payload.binary,
            content: payload.content || '',
            truncated: Boolean(payload.truncated),
            size: payload.size ?? row.size
          }
        } catch {
          return { ...base, text: false }
        }
      })
    )
    entries.value = loaded
    activePath.value = pickDefault(loaded)
  } catch (e) {
    if (e?.status === 404) {
      // 尚无能力包：允许从空文件树开始新增/上传
      entries.value = []
      activePath.value = ''
    } else {
      error.value = e.message || '无法加载包内文件'
    }
  } finally {
    loading.value = false
  }
}

function normalizePath(raw) {
  return String(raw || '').trim().replace(/\\/g, '/').replace(/^\/+/, '')
}

function addFile() {
  error.value = ''
  const path = normalizePath(newPath.value)
  if (!path || path.endsWith('/')) {
    error.value = '请填写文件路径，如 skills/demo/SKILL.md'
    return
  }
  if (!/^[\w.\-/]+$/.test(path) || path.split('/').includes('..')) {
    error.value = '路径只能包含字母、数字、下划线、短横线、点与斜杠'
    return
  }
  if (visibleEntries.value.some((e) => e.path === path)) {
    error.value = `已存在 ${path}`
    return
  }
  entries.value.push({ path, size: 0, text: true, content: '', truncated: false, isNew: true, deleted: false })
  activePath.value = path
  newPath.value = ''
}

function removeActive() {
  const entry = activeEntry.value
  if (!entry) return
  entry.deleted = true
  if (!entry.isNew) deleted.value = [...new Set([...deleted.value, entry.path])]
  activePath.value = pickDefault(visibleEntries.value)
}

async function save() {
  error.value = ''
  notice.value = ''
  saving.value = true
  try {
    const files = visibleEntries.value
      .filter((e) => e.text && !e.truncated)
      .map((e) => ({ path: e.path, content: e.content || '' }))
    await api.put(`/publish/capabilities/${props.capabilityId}/package`, {
      files,
      deleted: deleted.value
    })
    notice.value = '已保存到能力包'
    await load()
    emit('saved')
  } catch (e) {
    error.value = e.message
  } finally {
    saving.value = false
  }
}

function pickUpload() {
  uploadInput.value?.click()
}

async function onUpload(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  error.value = ''
  saving.value = true
  try {
    await api.upload(
      `/publish/capabilities/${props.capabilityId}/package/file`,
      file,
      uploadPath.value ? { path: normalizePath(uploadPath.value) } : {}
    )
    notice.value = `已上传 ${file.name}`
    uploadPath.value = ''
    await load()
    emit('saved')
  } catch (e) {
    error.value = e.message
  } finally {
    saving.value = false
  }
}

watch(() => props.capabilityId, load, { immediate: true })
</script>

<template>
  <div class="pkg-editor">
    <div v-if="error" class="alert alert-error mb-12">{{ error }}</div>
    <div v-if="notice" class="alert alert-success mb-12">{{ notice }}</div>

    <div class="toolbar">
      <div class="muted files-meta">
        <template v-if="artifactName">{{ artifactName }}<span v-if="artifactSize"> · {{ formatSize(artifactSize) }}</span> · </template>
        {{ visibleEntries.length }} 个文件
      </div>
      <template v-if="canEdit">
        <input
          v-model="newPath"
          class="input"
          style="max-width: 260px"
          placeholder="新增文件路径，如 skills/demo/SKILL.md"
          @keyup.enter="addFile"
        />
        <button class="btn btn-sm" type="button" @click="addFile">新增文件</button>
        <span class="sep"></span>
        <input v-model="uploadPath" class="input" style="max-width: 200px" placeholder="上传到路径（可空=原文件名）" />
        <button class="btn btn-sm" type="button" :disabled="saving" @click="pickUpload">上传文件</button>
        <input ref="uploadInput" type="file" style="display: none" @change="onUpload" />
        <button class="btn btn-sm btn-primary" type="button" :disabled="saving || loading" @click="save">
          {{ saving ? '保存中…' : '保存' }}
        </button>
      </template>
    </div>

    <div v-if="loading" class="muted state">加载包内文件…</div>
    <div v-else-if="visibleEntries.length === 0" class="muted state">包内无文件{{ canEdit ? '，可新增或上传文件' : '' }}</div>
    <div v-else class="pkg-split">
      <div class="pkg-tree">
        <div
          v-for="row in treeRows"
          :key="row.kind + ':' + row.path"
          class="tree-row"
          :class="[row.kind, { active: row.kind === 'file' && activePath === row.path }]"
          :style="{ paddingLeft: `${10 + row.depth * 14}px` }"
        >
          <span v-if="row.kind === 'dir'" class="tree-label"><span class="ico dir"></span>{{ row.name }}</span>
          <button v-else type="button" class="tree-file" @click="activePath = row.path">
            <span class="ico" :class="row.text ? 'doc' : 'bin'"></span>
            <span class="tree-name">{{ row.name }}</span>
          </button>
        </div>
      </div>
      <div class="pkg-view">
        <div class="view-head">
          <span class="view-path">{{ activePath || '选择文件' }}</span>
          <span v-if="activeEntry" class="muted view-size">
            {{ formatSize(activeEntry.size) }}
            <span v-if="activeEntry.truncated" class="warn"> · 文件过大，只读</span>
            <button
              v-if="canEdit && activeEntry.path !== 'plugin.json'"
              class="btn btn-sm"
              style="margin-left: 8px"
              type="button"
              @click="removeActive"
            >删除</button>
          </span>
        </div>
        <CodeEditor
          v-if="activeEntry && activeEntry.text && !activeEntry.truncated"
          :key="activeEntry.path"
          v-model="activeEntry.content"
          :language="languageFor(activeEntry.path)"
          :readonly="!canEdit"
          height="min(62vh, 720px)"
        />
        <pre v-else-if="activeEntry && activeEntry.text" class="view-code"><code>{{ activeEntry.content }}</code></pre>
        <div v-else-if="activeEntry" class="muted state">二进制文件，暂不支持在线编辑；可删除后用「上传文件」替换。</div>
        <div v-else class="muted state">选择左侧文件</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 10px;
}
.files-meta { font-size: 12px; margin-right: auto; }
.sep { width: 1px; height: 20px; background: var(--border); }
.warn { color: var(--warning); }
.pkg-split {
  display: grid;
  grid-template-columns: minmax(180px, 280px) 1fr;
  gap: 0;
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
  min-height: min(640px, calc(100vh - 260px));
  background: #fff;
}
.pkg-tree {
  border-right: 1px solid var(--border);
  background: #fafbfc;
  overflow: auto;
  max-height: min(72vh, 760px);
  padding: 6px 0;
}
.pkg-view { display: flex; flex-direction: column; min-width: 0; overflow: hidden; }
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
.ico { width: 12px; height: 12px; border-radius: 2px; flex: none; border: 1px solid var(--border); background: #fff; }
.ico.dir { background: #dbe4ff; border-color: #b6c7ff; }
.ico.doc { background: #e8f5ee; border-color: #b7dfc8; }
.ico.bin { background: #f1f5f9; }
.view-head {
  display: flex; justify-content: space-between; gap: 8px; align-items: center;
  padding: 8px 12px; border-bottom: 1px solid var(--border);
  background: #fafbfc; font-size: 12px;
}
.view-path { font-family: 'Cascadia Code', Consolas, monospace; color: var(--text); word-break: break-all; }
.view-size { flex: none; display: inline-flex; align-items: center; }
.view-code {
  margin: 0; padding: 12px 14px; overflow: auto; flex: 1;
  font-size: 12px; line-height: 1.5;
  font-family: 'Cascadia Code', Consolas, monospace;
  white-space: pre-wrap; word-break: break-word;
}
.state { padding: 24px 12px; text-align: center; font-size: 13px; }

@media (max-width: 720px) {
  .pkg-split { grid-template-columns: 1fr; }
  .pkg-tree { max-height: 220px; border-right: none; border-bottom: 1px solid var(--border); }
}
</style>
