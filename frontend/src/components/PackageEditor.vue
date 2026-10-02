<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../api'
import { formatSize } from '../utils/format'
import CodeEditor from './CodeEditor.vue'

const props = defineProps({
  capabilityId: { type: String, required: true },
  canEdit: { type: Boolean, default: false },
  type: { type: String, default: '' },
  name: { type: String, default: '' }
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
const newFolder = ref('')
const uploadBase = ref('')
const fileInput = ref(null)
const folderInput = ref(null)
const artifactName = ref('')
const artifactSize = ref(0)
const collapsed = ref(new Set())
const renaming = ref(false)
const renameValue = ref('')

const visibleEntries = computed(() => entries.value.filter((e) => !e.deleted))
const isSkill = computed(() => props.type === 'skill')
const activeEntry = computed(() => visibleEntries.value.find((e) => e.path === activePath.value) || null)

const treeRows = computed(() => {
  const list = visibleEntries.value.slice().sort((a, b) => a.path.localeCompare(b.path))
  const rows = []
  const seenDirs = new Set()
  for (const entry of list) {
    const parts = entry.path.split('/').filter(Boolean)
    let prefix = ''
    let hidden = false
    parts.forEach((part, idx) => {
      if (hidden) return
      const isLeaf = idx === parts.length - 1
      if (!isLeaf) {
        prefix = prefix ? `${prefix}/${part}` : part
        if (!seenDirs.has(prefix)) {
          seenDirs.add(prefix)
          rows.push({
            kind: 'dir',
            path: prefix,
            name: part,
            depth: idx,
            collapsed: collapsed.value.has(prefix)
          })
        }
        if (collapsed.value.has(prefix)) hidden = true
      } else {
        rows.push({ kind: 'file', path: entry.path, name: part, depth: idx, text: entry.text })
      }
    })
  }
  return rows
})

function languageFor(path) {
  const p = (path || '').toLowerCase()
  if (p.endsWith('.json')) return 'json'
  if (p.endsWith('.py')) return 'python'
  if (p.endsWith('.js') || p.endsWith('.mjs') || p.endsWith('.cjs')) return 'javascript'
  if (p.endsWith('.ts')) return 'typescript'
  if (p.endsWith('.yml') || p.endsWith('.yaml')) return 'yaml'
  if (p.endsWith('.sh')) return 'shell'
  if (p.endsWith('.md') || p.endsWith('.mdc') || p.endsWith('.markdown')) return 'markdown'
  return 'plaintext'
}

function pickDefault(list) {
  for (const pref of ['SKILL.md', 'plugin.json', 'README.md', 'command.json', 'tool.json']) {
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
  return String(raw || '').trim().replace(/\\/g, '/').replace(/^\/+/, '').replace(/\/+$/, '')
}

function validPath(path) {
  return Boolean(path) && /^[\w.\-/]+$/.test(path) && !path.split('/').includes('..')
}

function pushEntry(path, content, isNew = true) {
  entries.value.push({ path, size: content.length, text: true, content, truncated: false, isNew, deleted: false })
}

function joinPath(base, rel) {
  const b = normalizePath(base)
  const r = String(rel || '').replace(/\\/g, '/').replace(/^\/+/, '')
  return b ? `${b}/${r}` : r
}

function addFile() {
  error.value = ''
  const path = normalizePath(newPath.value)
  if (!path || !validPath(path)) {
    error.value = '请填写文件路径，如 references/guide.md（仅字母、数字、_、-、. 与 /）'
    return
  }
  if (visibleEntries.value.some((e) => e.path === path)) {
    error.value = `已存在 ${path}`
    return
  }
  pushEntry(path, '')
  activePath.value = path
  newPath.value = ''
}

function addFolder() {
  error.value = ''
  const dir = normalizePath(newFolder.value)
  if (!dir || !validPath(dir)) {
    error.value = '请填写文件夹路径，如 references（仅字母、数字、_、-、. 与 /）'
    return
  }
  const placeholder = `${dir}/.gitkeep`
  if (visibleEntries.value.some((e) => e.path === placeholder)) {
    error.value = `文件夹 ${dir} 已存在`
    return
  }
  pushEntry(placeholder, '')
  collapsed.value = new Set([...collapsed.value].filter((p) => p !== dir))
  activePath.value = placeholder
  newFolder.value = ''
}

function startAddUnder(dir) {
  newPath.value = `${dir}/`
  error.value = ''
}

function toggleDir(path) {
  const s = new Set(collapsed.value)
  s.has(path) ? s.delete(path) : s.add(path)
  collapsed.value = s
}

function startRename() {
  const entry = activeEntry.value
  if (!entry) return
  renaming.value = true
  renameValue.value = entry.path
}

function cancelRename() {
  renaming.value = false
  renameValue.value = ''
}

function confirmRename() {
  const entry = activeEntry.value
  if (!entry) return cancelRename()
  const next = normalizePath(renameValue.value)
  if (!next || !validPath(next)) {
    error.value = '非法路径'
    return
  }
  if (next === entry.path) return cancelRename()
  if (visibleEntries.value.some((e) => e.path === next)) {
    error.value = `已存在 ${next}`
    return
  }
  if (!entry.isNew) deleted.value = [...new Set([...deleted.value, entry.path])]
  entry.path = next
  activePath.value = next
  cancelRename()
}

function removeActive() {
  const entry = activeEntry.value
  if (!entry) return
  entry.deleted = true
  if (!entry.isNew) deleted.value = [...new Set([...deleted.value, entry.path])]
  activePath.value = pickDefault(visibleEntries.value)
}

function scaffoldSkill() {
  error.value = ''
  const nm = (props.name || 'skill').trim()
  const targets = [
    ['SKILL.md', `---\nname: ${nm}\ndescription: 一句话说明本技能做什么、何时使用。\n---\n\n# ${nm}\n\n## 何时使用\n\n（触发场景）\n\n## 步骤\n\n1. ...\n\n## 说明\n\n详见 \`references/\`。\n`],
    ['references/README.md', '# 参考资料\n\n把背景知识、规范、示例等放在 references/ 下（可含子目录），供模型按需读取。\n'],
    ['scripts/.gitkeep', ''],
    ['assets/.gitkeep', '']
  ]
  for (const [p, c] of targets) {
    if (!visibleEntries.value.some((e) => e.path === p)) pushEntry(p, c)
  }
  activePath.value = 'SKILL.md'
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

function pickFiles() {
  fileInput.value?.click()
}
function pickFolder() {
  folderInput.value?.click()
}

async function uploadFiles(fileList, { useRelative = false } = {}) {
  const arr = Array.from(fileList || [])
  if (!arr.length) return
  error.value = ''
  saving.value = true
  let ok = 0
  let fail = 0
  for (const file of arr) {
    const rel = useRelative && file.webkitRelativePath ? file.webkitRelativePath : file.name
    const target = joinPath(uploadBase.value, rel)
    try {
      await api.upload(`/publish/capabilities/${props.capabilityId}/package/file`, file, { path: target })
      ok += 1
    } catch {
      fail += 1
    }
  }
  saving.value = false
  notice.value = `已上传 ${ok} 个文件${fail ? `，失败 ${fail}` : ''}`
  uploadBase.value = ''
  await load()
  emit('saved')
}

async function onFileUpload(event) {
  const files = event.target.files
  event.target.value = ''
  await uploadFiles(files)
}

async function onFolderUpload(event) {
  const files = event.target.files
  event.target.value = ''
  await uploadFiles(files, { useRelative: true })
}

watch(() => props.capabilityId, load, { immediate: true })
</script>

<template>
  <div class="pkg-editor">
    <div v-if="error" class="alert alert-error mb-12">{{ error }}</div>
    <div v-if="notice" class="alert alert-success mb-12">{{ notice }}</div>

    <div class="pkg-toolbar">
      <div class="files-meta">
        <span class="dot"></span>
        <template v-if="artifactName">{{ artifactName }}<span v-if="artifactSize"> · {{ formatSize(artifactSize) }}</span> · </template>
        {{ visibleEntries.length }} 个文件
      </div>
      <div class="toolbar-actions">
        <template v-if="canEdit">
          <div class="combo">
            <input
              v-model="newPath"
              class="input"
              placeholder="新增文件，如 references/guide.md"
              @keyup.enter="addFile"
            />
            <button class="btn btn-sm" type="button" @click="addFile">新增文件</button>
          </div>
          <div class="combo">
            <input
              v-model="newFolder"
              class="input"
              placeholder="新增文件夹，如 references"
              @keyup.enter="addFolder"
            />
            <button class="btn btn-sm" type="button" @click="addFolder">新建文件夹</button>
          </div>
          <div class="combo">
            <input v-model="uploadBase" class="input" placeholder="上传到（可空=根）" />
            <button class="btn btn-sm" type="button" :disabled="saving" @click="pickFiles">上传文件</button>
            <button class="btn btn-sm" type="button" :disabled="saving" @click="pickFolder">上传文件夹</button>
          </div>
          <button v-if="isSkill" class="btn btn-sm" type="button" :disabled="saving" @click="scaffoldSkill">技能模板</button>
          <input ref="fileInput" type="file" multiple style="display: none" @change="onFileUpload" />
          <input ref="folderInput" type="file" webkitdirectory directory multiple style="display: none" @change="onFolderUpload" />
          <button class="btn btn-sm btn-primary" type="button" :disabled="saving || loading" @click="save">
            {{ saving ? '保存中…' : '保存' }}
          </button>
        </template>
      </div>
    </div>

    <div v-if="loading" class="muted state">加载包内文件…</div>
    <div v-else-if="visibleEntries.length === 0" class="empty-panel">
      <div class="empty-title">包内暂无文件</div>
      <div class="muted" style="font-size: 13px">
        {{ canEdit ? '可「新增文件 / 上传文件 / 上传文件夹」' : '该能力包为空' }}
        <template v-if="canEdit && isSkill">；技能可点「技能模板」一键生成 SKILL.md 与 references/scripts/assets</template>
      </div>
    </div>
    <div v-else class="pkg-split">
      <div class="pkg-tree">
        <div
          v-for="row in treeRows"
          :key="row.kind + ':' + row.path"
          class="tree-row"
          :class="[row.kind, { active: row.kind === 'file' && activePath === row.path }]"
          :style="{ paddingLeft: `${10 + row.depth * 14}px` }"
        >
          <button v-if="row.kind === 'dir'" type="button" class="tree-dir" @click="toggleDir(row.path)">
            <span class="caret" :class="{ open: !row.collapsed }"></span>
            <span class="ico dir"></span>
            <span class="tree-name">{{ row.name }}</span>
          </button>
          <button v-if="row.kind === 'dir'" class="dir-add" type="button" title="在此文件夹新增文件" @click="startAddUnder(row.path)">+</button>
          <button v-else type="button" class="tree-file" @click="activePath = row.path">
            <span class="ico" :class="row.text ? 'doc' : 'bin'"></span>
            <span class="tree-name">{{ row.name }}</span>
          </button>
        </div>
      </div>
      <div class="pkg-view">
        <div class="view-head">
          <template v-if="renaming">
            <input v-model="renameValue" class="input" style="max-width: 340px" @keyup.enter="confirmRename" @keyup.esc="cancelRename" />
            <span class="view-actions">
              <button class="btn btn-sm btn-primary" type="button" @click="confirmRename">确定</button>
              <button class="btn btn-sm" type="button" @click="cancelRename">取消</button>
            </span>
          </template>
          <template v-else>
            <span class="view-path">{{ activePath || '选择文件' }}</span>
            <span v-if="activeEntry" class="view-actions">
              <span class="view-size">
                {{ formatSize(activeEntry.size) }}
                <span v-if="activeEntry.truncated" class="warn"> · 文件过大，只读</span>
              </span>
              <template v-if="canEdit && activeEntry.path !== 'plugin.json'">
                <button class="btn btn-sm" type="button" @click="startRename">重命名</button>
                <button class="btn btn-sm btn-danger-ghost" type="button" @click="removeActive">删除</button>
              </template>
            </span>
          </template>
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
        <div v-else-if="activeEntry" class="muted state">二进制文件，暂不支持在线编辑；可重命名或删除后用「上传文件」替换。</div>
        <div v-else class="muted state">选择左侧文件</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pkg-toolbar {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 12px;
}
.files-meta { display: inline-flex; align-items: center; gap: 8px; font-size: 12px; color: var(--muted); margin-right: auto; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--primary, #4f6bed); flex: none; }
.toolbar-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.combo { display: inline-flex; align-items: stretch; }
.combo .input {
  border-top-right-radius: 0; border-bottom-right-radius: 0; min-width: 200px; max-width: 240px;
}
.combo .btn {
  border-top-left-radius: 0; border-bottom-left-radius: 0; border-left: none; white-space: nowrap;
}
.warn { color: var(--warning); }
.empty-panel {
  border: 1px dashed var(--border); border-radius: 12px; padding: 40px 16px;
  text-align: center; background: #fafbfc;
}
.empty-title { font-weight: 600; margin-bottom: 6px; }
.pkg-split {
  display: grid;
  grid-template-columns: minmax(200px, 300px) 1fr;
  gap: 0;
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
  min-height: min(640px, calc(100vh - 260px));
  background: #fff;
}
.pkg-tree {
  border-right: 1px solid var(--border);
  background: #fbfcfe;
  overflow: auto;
  max-height: min(72vh, 760px);
  padding: 8px 0;
}
.pkg-view { display: flex; flex-direction: column; min-width: 0; overflow: hidden; }
.tree-row { position: relative; display: flex; align-items: center; }
.tree-dir, .tree-file {
  display: flex; align-items: center; gap: 7px;
  width: 100%; border: none; background: none; text-align: left;
  padding: 5px 8px 5px 0; cursor: pointer; font-size: 12.5px; color: var(--text);
  border-radius: 6px; margin: 0 6px; min-width: 0;
}
.tree-dir { color: var(--muted); font-weight: 600; }
.tree-dir:hover, .tree-file:hover { background: #eef2ff; }
.tree-row.file.active .tree-file { background: #e7ecff; color: var(--primary); font-weight: 600; }
.caret {
  width: 0; height: 0; flex: none; border-left: 5px solid var(--muted);
  border-top: 4px solid transparent; border-bottom: 4px solid transparent;
  transition: transform .15s ease; transform: rotate(0deg);
}
.caret.open { transform: rotate(90deg); }
.tree-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dir-add {
  position: absolute; right: 8px; top: 50%; transform: translateY(-50%);
  width: 18px; height: 18px; line-height: 16px; text-align: center;
  border: 1px solid var(--border); background: #fff; border-radius: 5px;
  color: var(--muted); cursor: pointer; font-size: 13px; opacity: 0;
}
.tree-row.dir:hover .dir-add { opacity: 1; }
.dir-add:hover { color: var(--primary); border-color: var(--primary); }
.ico { width: 13px; height: 13px; border-radius: 3px; flex: none; border: 1px solid var(--border); background: #fff; }
.ico.dir { background: #dbe4ff; border-color: #b6c7ff; }
.ico.doc { background: #e8f5ee; border-color: #b7dfc8; }
.ico.bin { background: #f1f5f9; }
.view-head {
  display: flex; justify-content: space-between; gap: 10px; align-items: center;
  padding: 9px 12px; border-bottom: 1px solid var(--border);
  background: #fbfcfe; font-size: 12px; min-height: 42px;
}
.view-path { font-family: 'Cascadia Code', Consolas, monospace; color: var(--text); word-break: break-all; }
.view-actions { flex: none; display: inline-flex; align-items: center; gap: 8px; }
.view-size { color: var(--muted); }
.view-code {
  margin: 0; padding: 14px 16px; overflow: auto; flex: 1;
  font-size: 12.5px; line-height: 1.55;
  font-family: 'Cascadia Code', Consolas, monospace;
  white-space: pre-wrap; word-break: break-word;
}
.state { padding: 24px 12px; text-align: center; font-size: 13px; }

@media (max-width: 720px) {
  .pkg-split { grid-template-columns: 1fr; }
  .pkg-tree { max-height: 240px; border-right: none; border-bottom: 1px solid var(--border); }
  .combo .input { min-width: 140px; }
}
</style>
