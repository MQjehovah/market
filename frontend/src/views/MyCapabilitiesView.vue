<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import {
  TYPE_LABELS,
  TYPE_COLORS,
  VISIBILITY_LABELS,
  JOIN_VS_INSTALL_HINT,
  INSTALL_POLICY_LABELS,
  formatDate,
  shelfLabel,
  ownedTodoBucket,
  editRouteFor,
  canOnlineEdit,
  isLocalInstallKind,
  installCommandFor
} from '../utils/format'
import StatusBadge from '../components/StatusBadge.vue'
import CreateCapabilityModal from '../components/CreateCapabilityModal.vue'
import DebugCapabilityModal from '../components/DebugCapabilityModal.vue'
import ConfirmActionModal from '../components/ConfirmActionModal.vue'

const route = useRoute()
const router = useRouter()
/** mainTab: owned | added — 「我发布的 / 自定义」 */
const mainTab = ref('owned')
const caps = ref([])
const loading = ref(false)
const error = ref('')
const notice = ref('')
const showCreate = ref(false)
const initialShelf = ref('')
const debugCap = ref(null)
const sourceFilter = ref('all')
const filters = reactive({ q: '' })
const page = ref(1)
const pageSize = ref(20)
const confirmAction = ref(null)
const copiedId = ref('')

const CUSTOMIZE_TYPES = ['plugin', 'skill', 'mcp', 'rule', 'command', 'hook', 'agent']

const scopedCaps = computed(() => {
  if (mainTab.value === 'owned') return caps.value.filter((c) => c.owned)
  return caps.value.filter((c) => c.added)
})

const sourceChips = computed(() => {
  const list = scopedCaps.value
  const chips = [{ key: 'all', label: '全部', count: list.length }]
  if (mainTab.value === 'owned') {
    chips.push(
      { key: 'published', label: '已上架', count: list.filter((c) => c.status === 'published').length },
      { key: 'reviewing', label: '审核中', count: list.filter((c) => c.status === 'reviewing').length },
      {
        key: 'todo',
        label: '待处理',
        count: list.filter((c) => ['draft', 'returned', 'rejected'].includes(c.status)).length
      },
      {
        key: 'offline',
        label: '已下线',
        count: list.filter((c) => ['deprecated', 'archived'].includes(c.status)).length
      }
    )
  } else {
    CUSTOMIZE_TYPES.forEach((key) => {
      const count = list.filter((c) => c.type === key).length
      if (count) chips.push({ key: `type:${key}`, label: TYPE_LABELS[key], count })
    })
    const rest = list.filter((c) => !CUSTOMIZE_TYPES.includes(c.type)).length
    if (rest) chips.push({ key: 'type:other', label: '其他', count: rest })
  }
  return chips
})

const filteredCaps = computed(() => {
  const q = filters.q.trim().toLowerCase()
  return scopedCaps.value.filter((c) => {
    if (sourceFilter.value === 'published' && c.status !== 'published') return false
    if (sourceFilter.value === 'reviewing' && c.status !== 'reviewing') return false
    if (sourceFilter.value === 'todo' && !['draft', 'returned', 'rejected'].includes(c.status)) return false
    if (sourceFilter.value === 'offline' && !['deprecated', 'archived'].includes(c.status)) return false
    if (sourceFilter.value.startsWith('type:')) {
      const t = sourceFilter.value.slice(5)
      if (t === 'other') {
        if (CUSTOMIZE_TYPES.includes(c.type)) return false
      } else if (c.type !== t) return false
    }
    if (q) {
      const hay = `${c.name} ${c.description || ''} ${(c.tags || []).join(' ')}`.toLowerCase()
      if (!hay.includes(q)) return false
    }
    return true
  }).sort((a, b) => new Date(b.updated_at || 0) - new Date(a.updated_at || 0))
})

const totalPages = computed(() => Math.max(1, Math.ceil(filteredCaps.value.length / pageSize.value)))
const pagedCaps = computed(() => {
  const start = (page.value - 1) * pageSize.value
  return filteredCaps.value.slice(start, start + pageSize.value)
})

const tabCounts = computed(() => ({
  owned: caps.value.filter((c) => c.owned).length,
  added: caps.value.filter((c) => c.added).length
}))

function typeColor(type) {
  return TYPE_COLORS[type] || '#2f6bff'
}

function typeInitial(type) {
  return (TYPE_LABELS[type] || type || '?').slice(0, 1)
}

function sourceLabel(cap) {
  if (cap.owned) return '我创建的'
  if (cap.added) return '从目录加入'
  return '—'
}

function visibilityLabel(cap) {
  return VISIBILITY_LABELS[cap.visibility] || cap.visibility || '—'
}

function packageTargetId(cap) {
  return cap.has_draft && cap.draft_id ? cap.draft_id : cap.id
}

function goUploadPackage(cap) {
  router.push({ path: `/capabilities/${packageTargetId(cap)}`, query: { focus: 'package' } })
}

function editPath(cap) {
  return editRouteFor(cap)
}

async function load() {
  error.value = ''
  loading.value = true
  try {
    caps.value = await api.get('/my/capabilities?scope=all')
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function switchTab(tab) {
  mainTab.value = tab
  sourceFilter.value = 'all'
  page.value = 1
  filters.q = ''
}

function setSourceFilter(key) {
  sourceFilter.value = key
  page.value = 1
}

function onCreated(cap) {
  showCreate.value = false
  if (canOnlineEdit(cap.type)) {
    notice.value = `「${cap.name}」草稿已创建，正在打开在线编辑`
  } else {
    notice.value = `「${cap.name}」草稿已创建，请上传能力包并提交审核`
  }
  mainTab.value = 'owned'
  load()
}

async function submit(cap) {
  try {
    await api.post(`/publish/capabilities/${cap.id}/submit`)
    notice.value = `「${cap.name}」已提交审核`
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function submitDraft(cap) {
  try {
    await api.post(`/publish/capabilities/${cap.draft_id}/submit`)
    notice.value = `「${cap.name}」草稿 v${cap.draft_version} 已提交审核`
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function withdraw(cap) {
  try {
    await api.post(`/publish/capabilities/${cap.id}/withdraw`)
    notice.value = `「${cap.name}」已撤回审核，可修改类型或删除`
    await load()
  } catch (e) {
    error.value = e.message
  }
}

function askRemoveDraft(cap) {
  confirmAction.value = {
    kind: 'delete',
    title: '确定删除？',
    body: `删除「${cap.name} v${cap.version}」后，可使用该名称重新创建。`,
    okText: '删除',
    danger: true,
    cap
  }
}

function askRemoveFromMy(cap) {
  if (cap.removable === false || cap.install_policy === 'required') {
    error.value = `「${cap.name}」为必装能力，不能移除`
    return
  }
  confirmAction.value = {
    kind: 'remove',
    title: '确定移除？',
    body: `移除后，「${cap.name}」将不再出现在自定义列表中，不影响目录上架状态。`,
    okText: '移除',
    danger: false,
    cap
  }
}

async function toggleEnabled(cap) {
  if (cap.install_policy === 'required') {
    error.value = `「${cap.name}」为必装能力，不能停用`
    return
  }
  error.value = ''
  try {
    const next = cap.enabled === false
    const r = await api.patch(`/my/capabilities/${cap.id}`, { enabled: next })
    notice.value = r.message
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function copyInstall(cap) {
  const cmd = installCommandFor(cap)
  if (!cmd) return
  try {
    await navigator.clipboard.writeText(cmd)
    copiedId.value = cap.id
    notice.value = `已复制：${cmd}`
    setTimeout(() => {
      if (copiedId.value === cap.id) copiedId.value = ''
    }, 1600)
  } catch {
    notice.value = cmd
  }
}

async function confirmOk() {
  const action = confirmAction.value
  if (!action) return
  const cap = action.cap
  confirmAction.value = null
  try {
    if (action.kind === 'delete') {
      await api.delete(`/publish/capabilities/${cap.id}`)
      notice.value = `「${cap.name}」已删除`
    } else if (action.kind === 'remove') {
      const r = await api.delete(`/my/capabilities/${cap.id}`)
      notice.value = r.message
    }
    await load()
  } catch (e) {
    error.value = e.message
  }
}

function openCreate(shelf = '') {
  initialShelf.value = shelf || ''
  showCreate.value = true
}

function syncPublishQuery() {
  if (route.query.publish === '1') {
    initialShelf.value = String(route.query.shelf || '')
    showCreate.value = true
  }
}

function goPage(p) {
  if (p < 1 || p > totalPages.value) return
  page.value = p
}

watch(() => route.query.publish, syncPublishQuery)
watch([mainTab, sourceFilter, () => filters.q, pageSize], () => {
  page.value = 1
})
onMounted(() => {
  load()
  syncPublishQuery()
})
</script>

<template>
  <div class="my-page">
    <div class="page-head">
      <div>
        <h1 class="page-title">我的能力</h1>
        <p class="page-desc muted">
          「自定义」管理已加入能力的启用状态；「我发布的」走草稿与审核。{{ JOIN_VS_INSTALL_HINT }}
        </p>
      </div>
      <button class="btn btn-primary" type="button" @click="openCreate()">发布能力</button>
    </div>

    <div v-if="notice" class="alert alert-success mb-16">{{ notice }}</div>
    <div v-if="error" class="alert alert-error mb-16">{{ error }}</div>

    <div class="main-tabs">
      <button type="button" class="main-tab" :class="{ active: mainTab === 'owned' }" @click="switchTab('owned')">
        我发布的
        <span class="count">{{ tabCounts.owned }}</span>
      </button>
      <button type="button" class="main-tab" :class="{ active: mainTab === 'added' }" @click="switchTab('added')">
        自定义
        <span class="count">{{ tabCounts.added }}</span>
      </button>
    </div>

    <div class="filter-bar">
      <div class="source-chips">
        <button
          v-for="chip in sourceChips"
          :key="chip.key"
          type="button"
          class="source-chip"
          :class="{ active: sourceFilter === chip.key }"
          @click="setSourceFilter(chip.key)"
        >
          {{ chip.label }}
          <span class="chip-n">{{ chip.count }}</span>
        </button>
      </div>
      <input
        v-model="filters.q"
        class="input search-input"
        placeholder="搜索能力"
      />
    </div>

    <div class="panel table-panel">
      <div v-if="loading" class="empty">加载中…</div>
      <div v-else-if="caps.length === 0" class="empty">
        还没有能力。小白推荐路径：
        <ol style="text-align: left; display: inline-block; margin: 12px 0; padding-left: 20px">
          <li>点「发布能力」→ 选「发安装包」或「发助手 / 发组件」</li>
          <li>助手与组件：在线编辑（保存生成包）→ 提交审核；安装包：上传 zip → 提交审核</li>
          <li>上架后加入，本地执行 <code>cap install …</code></li>
        </ol>
        <div>
          <button class="btn btn-primary" type="button" @click="openCreate('install')">发安装包</button>
          <a href="/" style="margin-left: 12px; color: var(--primary)">去发现逛逛</a>
        </div>
      </div>
      <div v-else-if="filteredCaps.length === 0 && mainTab === 'added'" class="empty">
        还没有加入任何能力。去发现页加入后，可在此启用/停用。
        <div style="margin-top: 12px"><a href="/">去发现逛逛</a></div>
      </div>
      <div v-else-if="filteredCaps.length === 0" class="empty">没有符合筛选条件的能力</div>
      <table v-else-if="mainTab === 'added'" class="skill-table customize-table">
        <thead>
          <tr>
            <th style="width: 72px">启用</th>
            <th>能力</th>
            <th>类型</th>
            <th>更新</th>
            <th style="width: 200px">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="cap in pagedCaps" :key="cap.id" :class="{ dim: cap.enabled === false }">
            <td>
              <button
                type="button"
                class="toggle"
                :class="{ on: cap.enabled !== false, locked: cap.install_policy === 'required' }"
                :disabled="cap.install_policy === 'required'"
                :title="cap.install_policy === 'required' ? '必装，不能停用' : (cap.enabled === false ? '点击启用' : '点击停用')"
                @click="toggleEnabled(cap)"
              >
                <span class="toggle-knob" />
              </button>
            </td>
            <td>
              <div class="skill-cell">
                <div class="skill-icon" :style="{ background: typeColor(cap.type) }">{{ typeInitial(cap.type) }}</div>
                <div class="skill-meta">
                  <div class="skill-name-row">
                    <router-link class="skill-name" :to="`/capabilities/${cap.id}`">{{ cap.name }}</router-link>
                    <span class="ver-tag">v{{ cap.version }}</span>
                  </div>
                  <div class="skill-desc muted">{{ cap.description || TYPE_LABELS[cap.type] }}</div>
                </div>
              </div>
            </td>
            <td><span class="vis-pill">{{ TYPE_LABELS[cap.type] }}</span></td>
            <td class="muted time">{{ formatDate(cap.updated_at) }}</td>
            <td>
              <div class="ops">
                <button
                  v-if="isLocalInstallKind(cap.type)"
                  class="op-link"
                  type="button"
                  @click="copyInstall(cap)"
                >{{ copiedId === cap.id ? '已复制' : '复制安装命令' }}</button>
                <router-link :to="`/capabilities/${cap.id}`" class="op-link">详情</router-link>
                <button
                  v-if="cap.removable !== false && cap.install_policy !== 'required'"
                  class="op-link danger"
                  type="button"
                  @click="askRemoveFromMy(cap)"
                >移除</button>
                <span v-else class="muted" style="font-size: 12px">必装</span>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <table v-else class="skill-table">
        <thead>
          <tr>
            <th style="width: 36%">能力</th>
            <th>状态</th>
            <th>可见范围</th>
            <th>来源</th>
            <th>更新时间</th>
            <th style="width: 160px">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="cap in pagedCaps" :key="cap.id">
            <td>
              <div class="skill-cell">
                <div class="skill-icon" :style="{ background: typeColor(cap.type) }">{{ typeInitial(cap.type) }}</div>
                <div class="skill-meta">
                  <div class="skill-name-row">
                    <router-link class="skill-name" :to="`/capabilities/${cap.id}`">{{ cap.name }}</router-link>
                    <span class="ver-tag">v{{ cap.version }}</span>
                    <span v-if="cap.has_draft" class="badge badge-warning">草稿 v{{ cap.draft_version }}</span>
                  </div>
                  <div class="skill-desc muted">
                    {{ cap.description || TYPE_LABELS[cap.type] }}
                    <span v-if="shelfLabel(cap.type)"> · {{ shelfLabel(cap.type) }}</span>
                  </div>
                </div>
              </div>
            </td>
            <td><StatusBadge :status="cap.status" /></td>
            <td>
              <span class="vis-pill">{{ visibilityLabel(cap) }}</span>
            </td>
            <td>
              <span class="src-text">{{ sourceLabel(cap) }}</span>
            </td>
            <td class="muted time">{{ formatDate(cap.updated_at) }}</td>
            <td>
              <div class="ops">
                <router-link :to="`/capabilities/${cap.id}`" class="op-link">详情</router-link>
                <button
                  v-if="['published', 'deprecated'].includes(cap.status)"
                  class="op-link"
                  type="button"
                  @click="debugCap = cap"
                >{{ cap.type === 'mcp' ? '试用连接器' : cap.type === 'agent' ? '试用助手' : '试用' }}</button>
                <template v-if="cap.has_draft">
                  <router-link v-if="editPath(cap)" :to="editPath(cap)" class="op-link">在线编辑</router-link>
                  <button
                    v-if="ownedTodoBucket(cap) === 'missing_package'"
                    class="op-link"
                    type="button"
                    @click="goUploadPackage(cap)"
                  >{{ canOnlineEdit(cap.type) ? '上传 zip' : '去上传能力包' }}</button>
                  <button
                    v-else
                    class="op-link success"
                    type="button"
                    @click="submitDraft(cap)"
                  >提交审核</button>
                </template>
                <template v-else-if="cap.owned && ['draft', 'returned', 'rejected'].includes(cap.status)">
                  <router-link v-if="editPath(cap)" :to="editPath(cap)" class="op-link">在线编辑</router-link>
                  <button
                    v-if="ownedTodoBucket(cap) === 'missing_package'"
                    class="op-link"
                    type="button"
                    @click="goUploadPackage(cap)"
                  >{{ canOnlineEdit(cap.type) ? '上传 zip' : '去上传能力包' }}</button>
                  <button
                    v-else
                    class="op-link success"
                    type="button"
                    @click="submit(cap)"
                  >提交审核</button>
                  <button class="op-link danger" type="button" @click="askRemoveDraft(cap)">删除</button>
                </template>
                <template v-else-if="cap.owned && cap.status === 'reviewing'">
                  <button class="op-link" type="button" @click="withdraw(cap)">撤回</button>
                  <button class="op-link danger" type="button" @click="askRemoveDraft(cap)">删除</button>
                </template>
                <button
                  v-if="cap.added && cap.removable !== false && cap.install_policy !== 'required'"
                  class="op-link danger"
                  type="button"
                  @click="askRemoveFromMy(cap)"
                >移除</button>
                <span
                  v-else-if="cap.added"
                  class="muted"
                  style="font-size: 12px"
                  :title="INSTALL_POLICY_LABELS[cap.install_policy] || ''"
                >必装</span>
              </div>
            </td>
          </tr>
        </tbody>
      </table>

      <div v-if="filteredCaps.length > 0" class="pager">
        <span class="muted">共 {{ filteredCaps.length }} 条</span>
        <div class="pager-nav">
          <button class="btn btn-sm" type="button" :disabled="page <= 1" @click="goPage(page - 1)">上一页</button>
          <span class="page-n">{{ page }}</span>
          <button class="btn btn-sm" type="button" :disabled="page >= totalPages" @click="goPage(page + 1)">下一页</button>
        </div>
        <select v-model.number="pageSize" class="select page-size">
          <option :value="10">10 条/页</option>
          <option :value="20">20 条/页</option>
          <option :value="50">50 条/页</option>
        </select>
      </div>
    </div>

    <ConfirmActionModal
      :show="!!confirmAction"
      :title="confirmAction?.title || ''"
      :body="confirmAction?.body || ''"
      :ok-text="confirmAction?.okText || '确定'"
      :danger="!!confirmAction?.danger"
      @ok="confirmOk"
      @cancel="confirmAction = null"
    />

    <CreateCapabilityModal
      :show="showCreate"
      :initial-shelf="initialShelf"
      @close="showCreate = false"
      @created="onCreated"
    />
    <DebugCapabilityModal
      :show="!!debugCap"
      :cap="debugCap"
      :title="debugCap?.type === 'agent' ? '试用助手' : (debugCap?.type === 'mcp' ? '试用连接器' : '云端试用')"
      @close="debugCap = null"
    />
  </div>
</template>

<style scoped>
.page-head {
  display: flex; align-items: flex-end; justify-content: space-between;
  gap: 16px; margin-bottom: 18px;
}
.page-title { margin: 0; font-size: 24px; font-weight: 700; letter-spacing: -0.02em; }
.page-desc { margin: 6px 0 0; font-size: 13px; }
.main-tabs {
  display: flex; gap: 2px; border-bottom: 1px solid var(--border); margin-bottom: 14px;
}
.main-tab {
  background: none; border: none; color: var(--muted);
  padding: 10px 16px; cursor: pointer; font-size: 14px;
  border-bottom: 2px solid transparent; display: inline-flex; align-items: center; gap: 6px;
}
.main-tab.active { color: var(--primary); border-bottom-color: var(--primary); font-weight: 600; }
.main-tab .count {
  min-width: 18px; height: 18px; padding: 0 5px; border-radius: 9px;
  background: var(--panel-2); color: var(--muted); font-size: 11px;
  display: inline-flex; align-items: center; justify-content: center;
}
.main-tab.active .count { background: var(--primary-soft); color: var(--primary); }

.filter-bar {
  display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
  gap: 12px; margin-bottom: 14px;
}
.source-chips { display: flex; flex-wrap: wrap; gap: 8px; }
.source-chip {
  background: #fff; border: 1px solid var(--border); color: var(--muted);
  padding: 6px 12px; border-radius: 999px; cursor: pointer; font-size: 13px;
  display: inline-flex; align-items: center; gap: 6px;
}
.source-chip:hover { border-color: var(--primary); color: var(--text); }
.source-chip.active {
  background: var(--primary-soft); border-color: #c9d8ff; color: var(--primary); font-weight: 600;
}
.chip-n { font-variant-numeric: tabular-nums; opacity: 0.85; }
.search-input { max-width: 240px; }

.table-panel { padding: 0; overflow: hidden; }
.skill-table { width: 100%; border-collapse: collapse; }
.skill-table th {
  text-align: left; padding: 12px 16px; font-size: 12px; font-weight: 500;
  color: var(--muted); background: #fafbfc; border-bottom: 1px solid var(--border);
}
.skill-table td {
  padding: 14px 16px; border-bottom: 1px solid var(--border); vertical-align: middle;
  font-size: 13px;
}
.skill-table tr:hover td { background: #fafbfc; }
.skill-table tr:last-child td { border-bottom: none; }

.skill-cell { display: flex; gap: 12px; align-items: flex-start; min-width: 0; }
.skill-icon {
  width: 40px; height: 40px; border-radius: 10px; flex: none;
  color: #fff; font-weight: 700; font-size: 16px;
  display: flex; align-items: center; justify-content: center;
}
.skill-meta { min-width: 0; }
.skill-name-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.skill-name { color: var(--text); font-weight: 650; font-size: 14px; }
.skill-name:hover { color: var(--primary); }
.ver-tag {
  font-size: 11px; color: var(--muted); background: var(--panel-2);
  border: 1px solid var(--border); border-radius: 6px; padding: 1px 6px;
}
.skill-desc {
  margin-top: 4px; font-size: 12px; line-height: 1.45;
  display: -webkit-box; -webkit-line-clamp: 1; line-clamp: 1; -webkit-box-orient: vertical; overflow: hidden;
}
.vis-pill {
  display: inline-flex; padding: 2px 8px; border-radius: 999px;
  background: var(--panel-2); border: 1px solid var(--border); font-size: 12px; color: var(--text);
}
.src-text { color: var(--text); }
.time { white-space: nowrap; font-size: 12px; }
.ops { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.op-link {
  background: none; border: none; padding: 0; cursor: pointer;
  color: var(--primary); font-size: 13px; text-decoration: none;
}
.op-link:hover { text-decoration: underline; }
.op-link.danger { color: var(--danger); }
.op-link.success { color: var(--success); }

.pager {
  display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
  gap: 12px; padding: 12px 16px; border-top: 1px solid var(--border);
}
.pager-nav { display: flex; align-items: center; gap: 8px; }
.page-n {
  min-width: 28px; height: 28px; border-radius: 8px;
  background: var(--primary); color: #fff;
  display: inline-flex; align-items: center; justify-content: center; font-size: 13px;
}
.page-size { max-width: 110px; }

.toggle {
  width: 40px; height: 22px; border-radius: 999px; border: none;
  background: #cfd6e0; position: relative; cursor: pointer; padding: 0;
  transition: background .15s ease;
}
.toggle.on { background: var(--primary); }
.toggle.locked { opacity: 0.55; cursor: not-allowed; }
.toggle-knob {
  position: absolute; top: 2px; left: 2px;
  width: 18px; height: 18px; border-radius: 50%; background: #fff;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.2);
  transition: left .15s ease;
}
.toggle.on .toggle-knob { left: 20px; }
.dim { opacity: 0.55; }
.customize-table td { padding-top: 12px; padding-bottom: 12px; }

.confirm-mask {
  position: fixed; inset: 0; background: var(--overlay);
  display: flex; align-items: center; justify-content: center; z-index: 80; padding: 16px;
}
.confirm-card {
  width: min(400px, 100%); background: #fff; border-radius: 14px;
  padding: 20px; box-shadow: var(--shadow-lg); border: 1px solid var(--border);
}
.confirm-title {
  display: flex; align-items: center; gap: 8px;
  font-size: 16px; font-weight: 650;
}
.confirm-warn {
  width: 22px; height: 22px; border-radius: 50%;
  background: rgba(245, 165, 36, 0.15); color: #b7791f;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 13px; font-weight: 700;
}
.confirm-body { margin: 12px 0 0; font-size: 13px; line-height: 1.55; }
.confirm-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 18px; }
.mb-16 { margin-bottom: 16px; }

@media (max-width: 900px) {
  .skill-table th:nth-child(3),
  .skill-table td:nth-child(3),
  .skill-table th:nth-child(4),
  .skill-table td:nth-child(4) { display: none; }
}
</style>
