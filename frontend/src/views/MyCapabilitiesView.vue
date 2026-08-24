<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import { TYPE_LABELS, formatDate } from '../utils/format'
import StatusBadge from '../components/StatusBadge.vue'
import CreateCapabilityModal from '../components/CreateCapabilityModal.vue'
import DebugCapabilityModal from '../components/DebugCapabilityModal.vue'

const scope = ref('all')
const caps = ref([])
const error = ref('')
const notice = ref('')
const showCreate = ref(false)
const debugCap = ref(null)
const filters = reactive({ q: '', type: '', category: '', status: '', sort: 'updated' })

const counts = computed(() => ({
  all: caps.value.length,
  owned: caps.value.filter((c) => c.owned).length,
  added: caps.value.filter((c) => c.added).length
}))

const categoryOptions = computed(() =>
  [...new Set(caps.value.map((c) => c.category).filter(Boolean))].sort()
)

const filteredCaps = computed(() => {
  const q = filters.q.trim().toLowerCase()
  const list = caps.value.filter((c) => {
    if (filters.type && c.type !== filters.type) return false
    if (filters.category && c.category !== filters.category) return false
    if (filters.status && c.status !== filters.status) return false
    if (q) {
      const hay = `${c.name} ${c.description || ''} ${(c.tags || []).join(' ')}`.toLowerCase()
      if (!hay.includes(q)) return false
    }
    return true
  })
  if (filters.sort === 'name') {
    list.sort((a, b) => a.name.localeCompare(b.name, 'zh'))
  } else if (filters.sort === 'usage') {
    list.sort((a, b) => (b.usage_count || 0) - (a.usage_count || 0))
  } else if (filters.sort === 'rating') {
    list.sort((a, b) => (b.avg_rating || 0) - (a.avg_rating || 0))
  } else {
    list.sort((a, b) => new Date(b.updated_at || 0) - new Date(a.updated_at || 0))
  }
  return list
})

async function load() {
  error.value = ''
  try {
    caps.value = await api.get(`/my/capabilities?scope=${scope.value}`)
  } catch (e) {
    error.value = e.message
  }
}

function onCreated(cap) {
  showCreate.value = false
  notice.value = `「${cap.name}」草稿已创建，可在详情页上传能力包并提交审核`
  load()
}

function switchScope(s) {
  scope.value = s
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

async function removeDraft(cap) {
  if (!confirm(`确认删除「${cap.name} v${cap.version}」？删除后可使用该名称重新创建。`)) return
  try {
    await api.delete(`/publish/capabilities/${cap.id}`)
    notice.value = `「${cap.name}」已删除`
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function removeFromMy(cap) {
  try {
    const r = await api.delete(`/my/capabilities/${cap.id}`)
    notice.value = r.message
    await load()
  } catch (e) {
    error.value = e.message
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="flex-between mb-16" style="align-items: flex-end">
      <div>
        <h2 style="margin: 0">我的能力</h2>
        <div class="muted" style="font-size: 13px">
          我创建的 + 从市场加入的能力；自己的能力可直接调用调试，无需管理员
        </div>
      </div>
      <button class="btn btn-primary" @click="showCreate = true">+ 新建能力</button>
    </div>

    <div v-if="notice" class="alert alert-success mb-16">{{ notice }}</div>
    <div v-if="error" class="alert alert-error mb-16">{{ error }}</div>

    <div class="tabs">
      <button class="tab" :class="{ active: scope === 'all' }" @click="switchScope('all')">
        全部 <span class="tab-count">{{ counts.all }}</span>
      </button>
      <button class="tab" :class="{ active: scope === 'owned' }" @click="switchScope('owned')">
        我创建的 <span class="tab-count">{{ counts.owned }}</span>
      </button>
      <button class="tab" :class="{ active: scope === 'added' }" @click="switchScope('added')">
        从市场加入 <span class="tab-count">{{ counts.added }}</span>
      </button>
    </div>

    <div class="panel toolbar mt-16">
      <input
        v-model="filters.q"
        class="input"
        style="max-width: 260px"
        placeholder="搜索名称 / 描述 / 标签…"
      />
      <select v-model="filters.type" class="select" style="max-width: 140px" @change="filters.category = ''">
        <option value="">全部类型</option>
        <option v-for="(label, key) in TYPE_LABELS" :key="key" :value="key">{{ label }}</option>
      </select>
      <select v-model="filters.category" class="select" style="max-width: 150px">
        <option value="">全部分类</option>
        <option v-for="c in categoryOptions" :key="c" :value="c">{{ c }}</option>
      </select>
      <select v-model="filters.status" class="select" style="max-width: 130px">
        <option value="">全部状态</option>
        <option value="draft">草稿</option>
        <option value="reviewing">待审</option>
        <option value="published">正式版</option>
        <option value="deprecated">已弃用</option>
        <option value="archived">已归档</option>
        <option value="rejected">已驳回</option>
        <option value="returned">已打回</option>
      </select>
      <select v-model="filters.sort" class="select" style="max-width: 130px">
        <option value="updated">最近更新</option>
        <option value="name">名称</option>
        <option value="usage">使用最多</option>
        <option value="rating">评分最高</option>
      </select>
      <button class="btn" @click="Object.assign(filters, { q: '', type: '', category: '', status: '', sort: 'updated' })">重置</button>
      <span class="muted" style="font-size: 12px">共 {{ filteredCaps.length }} 项</span>
    </div>

    <div class="panel mt-16">
      <div v-if="caps.length === 0" class="empty">
        还没有能力。去<a href="/" style="color: var(--primary)">能力市场</a>逛逛，或点击右上角「+ 新建能力」。
      </div>
      <div v-else-if="filteredCaps.length === 0" class="empty">没有符合筛选条件的能力</div>
      <table v-else class="table">
        <thead>
          <tr><th>能力</th><th>来源</th><th>类型</th><th>版本</th><th>状态</th><th>使用量</th><th>更新时间</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="cap in filteredCaps" :key="cap.id">
            <td><router-link :to="`/capabilities/${cap.id}`">{{ cap.name }}</router-link></td>
            <td>
              <span v-if="cap.owned" class="badge badge-primary">我创建的</span>
              <span v-if="cap.added" class="badge">从市场加入</span>
            </td>
            <td>{{ TYPE_LABELS[cap.type] }}</td>
            <td>v{{ cap.version }}</td>
            <td><StatusBadge :status="cap.status" /></td>
            <td>{{ cap.usage_count }}</td>
            <td class="muted">{{ formatDate(cap.updated_at) }}</td>
            <td>
              <div class="flex" style="gap: 6px; flex-wrap: wrap">
                <router-link :to="`/capabilities/${cap.id}`" class="btn btn-sm">详情</router-link>
                <button v-if="['published', 'deprecated'].includes(cap.status)" class="btn btn-sm btn-primary" @click="debugCap = cap">
                  调用 / 调试
                </button>
                <template v-if="cap.has_draft">
                  <span class="badge badge-warning">草稿 v{{ cap.draft_version }}</span>
                  <button class="btn btn-sm btn-success" @click="submitDraft(cap)">提交草稿审核</button>
                  <router-link v-if="cap.type === 'skill'" :to="`/skills/${encodeURIComponent(cap.name)}/edit`" class="btn btn-sm">编辑草稿</router-link>
                  <router-link v-else-if="cap.type === 'agent'" :to="`/agents/${encodeURIComponent(cap.name)}/edit`" class="btn btn-sm">编辑草稿</router-link>
                  <router-link v-else-if="cap.type === 'tool'" :to="`/tools/${encodeURIComponent(cap.name)}/edit`" class="btn btn-sm">编辑草稿</router-link>
                  <router-link v-else-if="cap.type === 'mcp'" :to="`/mcp/${encodeURIComponent(cap.name)}/edit`" class="btn btn-sm">编辑草稿</router-link>
                  <router-link v-else-if="cap.type === 'workflow'" :to="`/workflows/${cap.draft_id}/edit`" class="btn btn-sm">可视化编辑</router-link>
                  <router-link :to="`/capabilities/${cap.draft_id}`" class="btn btn-sm">查看草稿</router-link>
                </template>
                <template v-else-if="cap.owned && ['draft', 'returned', 'rejected'].includes(cap.status)">
                  <button class="btn btn-sm btn-success" @click="submit(cap)">提交审核</button>
                  <router-link :to="`/capabilities/${cap.id}`" class="btn btn-sm">编辑类型</router-link>
                  <router-link v-if="cap.type === 'skill'" :to="`/skills/${encodeURIComponent(cap.name)}/edit`" class="btn btn-sm">编辑草稿</router-link>
                  <router-link v-else-if="cap.type === 'agent'" :to="`/agents/${encodeURIComponent(cap.name)}/edit`" class="btn btn-sm">编辑草稿</router-link>
                  <router-link v-else-if="cap.type === 'tool'" :to="`/tools/${encodeURIComponent(cap.name)}/edit`" class="btn btn-sm">编辑草稿</router-link>
                  <router-link v-else-if="cap.type === 'mcp'" :to="`/mcp/${encodeURIComponent(cap.name)}/edit`" class="btn btn-sm">编辑草稿</router-link>
                  <router-link v-if="cap.type === 'workflow'" :to="`/workflows/${cap.id}/edit`" class="btn btn-sm">可视化编辑</router-link>
                  <button class="btn btn-sm btn-danger" @click="removeDraft(cap)">删除</button>
                </template>
                <template v-else-if="cap.owned && cap.status === 'reviewing'">
                  <button class="btn btn-sm" @click="withdraw(cap)">撤回审核</button>
                  <button class="btn btn-sm btn-danger" @click="removeDraft(cap)">删除</button>
                </template>
                <button v-if="cap.added && !cap.owned" class="btn btn-sm" @click="removeFromMy(cap)">移除</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <CreateCapabilityModal :show="showCreate" @close="showCreate = false" @created="onCreated" />
    <DebugCapabilityModal :show="!!debugCap" :cap="debugCap" @close="debugCap = null" />
  </div>
</template>

<style scoped>
.tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--border); }
.tab {
  background: none; border: none; color: var(--muted); padding: 10px 16px; cursor: pointer;
  border-bottom: 2px solid transparent; font-size: 14px;
}
.tab.active { color: var(--primary); border-bottom-color: var(--primary); }
.tab-count {
  display: inline-flex; min-width: 18px; height: 18px; border-radius: 9px; align-items: center;
  justify-content: center; background: var(--panel-2); color: var(--muted); font-size: 11px; margin-left: 4px;
}
.tab.active .tab-count { background: rgba(79,140,255,.15); color: var(--primary); }
.field { display: flex; flex-direction: column; gap: 6px; }
.field label { font-size: 13px; color: var(--muted); }
</style>
