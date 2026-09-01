<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { authState } from '../stores/auth'
import {
  DEFAULT_BROWSE_KINDS,
  MORE_BROWSE_KINDS,
  SHELVES,
  TYPE_CATEGORIES,
  TYPE_LABELS
} from '../utils/format'
import CapabilityCard from '../components/CapabilityCard.vue'

const router = useRouter()
const route = useRoute()
const isLoggedIn = computed(() => Boolean(authState.token))
const showAdvanced = ref(false)

const caps = ref([])
const hotCaps = ref([])
const ratedCaps = ref([])
const featuredCaps = ref([])
const loading = ref(false)
const total = ref(0)
const page = ref(1)
const pageSize = 12
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))

const filters = reactive({
  q: '',
  shelf: '',
  type: '',
  category: '',
  skill: '',
  mcp: '',
  sort: 'latest',
  tab: '' // '', skill, install, agent, more
})
const myIds = ref(new Set())
const notice = ref('')

const browseTabs = [
  { key: '', label: '推荐', hint: '技能、安装包与助手的精选与热门' },
  { key: 'skill', label: '技能', hint: '可复用 SOP（SKILL.md）' },
  { key: 'install', label: '安装包', hint: SHELVES.install.description },
  { key: 'agent', label: '助手', hint: '面向场景的 Agent 人设' },
  { key: 'more', label: '更多', hint: '连接器、能力编排、编排函数' }
]

/** 推荐页：无货架、无类型、无搜索、无其它筛选 */
const showDiscovery = computed(
  () =>
    !filters.q &&
    !filters.shelf &&
    !filters.type &&
    !filters.tab &&
    !filters.category &&
    !filters.skill &&
    !filters.mcp &&
    page.value === 1
)

const showCatalog = computed(() => !showDiscovery.value)

const pageContext = computed(() => {
  if (filters.q) {
    return {
      eyebrow: '搜索',
      title: '搜索结果',
      desc: '跨目录查找能力。'
    }
  }
  if (filters.tab === 'skill' || filters.type === 'skill') {
    return { eyebrow: '目录', title: '技能', desc: '可复用 SOP；加入后用 cap install --type skill，或装进助手/安装包。' }
  }
  if (filters.tab === 'install' || filters.shelf === 'install') {
    return { eyebrow: '目录', title: '安装包', desc: SHELVES.install.description }
  }
  if (filters.tab === 'agent' || filters.type === 'agent') {
    return { eyebrow: '目录', title: '助手', desc: '场景级 Agent；依赖的技能/连接器需已上架，或改用安装包内嵌。' }
  }
  if (filters.tab === 'more' || MORE_BROWSE_KINDS.includes(filters.type)) {
    return {
      eyebrow: '目录',
      title: '更多',
      desc: '连接器、能力编排与编排函数；进阶发布与编排用。'
    }
  }
  if (filters.shelf && SHELVES[filters.shelf]) {
    const s = SHELVES[filters.shelf]
    return { eyebrow: '分类', title: s.label, desc: s.description }
  }
  if (filters.shelf === 'all') {
    return { eyebrow: '目录', title: '搜索结果', desc: '含技能、安装包、助手与更多类型。' }
  }
  return {
    eyebrow: '企业内部能力目录',
    title: '发现当下值得使用的能力',
    desc: '优先看技能与安装包；加入后用 cap install 装到零号员工 / IDE（加入≠安装）。'
  }
})

const catalogTitle = computed(() => {
  if (filters.q) return '搜索结果'
  if (filters.type && TYPE_LABELS[filters.type]) return TYPE_LABELS[filters.type]
  if (filters.shelf && SHELVES[filters.shelf]) return SHELVES[filters.shelf].label
  return '筛选结果'
})

const typeOptions = computed(() => {
  if (filters.tab === 'more') return [...MORE_BROWSE_KINDS]
  if (filters.shelf && filters.shelf !== 'all' && SHELVES[filters.shelf]) {
    return SHELVES[filters.shelf].kinds
  }
  if (filters.type) return [filters.type]
  if (!filters.shelf) return [...DEFAULT_BROWSE_KINDS]
  return Object.keys(TYPE_CATEGORIES)
})

const categoryOptions = computed(() => {
  const kinds = typeOptions.value
  const set = new Set()
  kinds.forEach((k) => (TYPE_CATEGORIES[k] || []).forEach((c) => set.add(c)))
  return [...set]
})

const activeBrowseTab = computed(() => {
  if (filters.q && filters.shelf === 'all') return ''
  if (filters.tab) return filters.tab
  if (filters.type === 'skill') return 'skill'
  if (filters.type === 'agent') return 'agent'
  if (MORE_BROWSE_KINDS.includes(filters.type)) return 'more'
  if (filters.shelf === 'install') return 'install'
  if (filters.shelf === 'all') return ''
  return ''
})

async function fetchList(extra = {}) {
  const params = new URLSearchParams()
  if (filters.q) params.set('q', filters.q)
  if (filters.type) {
    params.set('type', filters.type)
  } else if (filters.shelf === 'all') {
    params.set('include_bricks', 'true')
  } else if (filters.shelf) {
    params.set('shelf', filters.shelf)
  }
  if (filters.category) params.set('category', filters.category)
  if (filters.skill) params.set('skill', filters.skill)
  if (filters.mcp) params.set('mcp', filters.mcp)
  params.set('sort', extra.sort || filters.sort)
  params.set('page', String(extra.page || page.value))
  params.set('page_size', String(extra.page_size || pageSize))
  return api.get(`/capabilities?${params.toString()}`)
}

async function loadDiscovery() {
  if (!showDiscovery.value) {
    hotCaps.value = []
    ratedCaps.value = []
    featuredCaps.value = []
    return
  }
  try {
    const [hot, rated] = await Promise.all([
      fetchList({ sort: 'usage', page: 1, page_size: 6 }),
      fetchList({ sort: 'rating', page: 1, page_size: 6 })
    ])
    hotCaps.value = hot.items || []
    ratedCaps.value = rated.items || []
    const seen = new Set()
    const pool = [...(rated.items || []), ...(hot.items || [])]
    featuredCaps.value = pool
      .filter((c) => {
        if (seen.has(c.id)) return false
        seen.add(c.id)
        return (Number(c.avg_rating) || 0) >= 4
      })
      .slice(0, 6)
    if (featuredCaps.value.length < 3) {
      featuredCaps.value = (rated.items || []).slice(0, 6)
    }
  } catch {
    hotCaps.value = []
    ratedCaps.value = []
    featuredCaps.value = []
  }
}

async function load() {
  loading.value = true
  try {
    if (showDiscovery.value) {
      caps.value = []
      total.value = 0
      await loadDiscovery()
      return
    }
    const body = await fetchList()
    caps.value = body.items
    total.value = body.total
    hotCaps.value = []
    ratedCaps.value = []
    featuredCaps.value = []
  } finally {
    loading.value = false
  }
}

async function loadMy() {
  if (!authState.token) return
  try {
    const items = await api.get('/my/capabilities')
    myIds.value = new Set(items.map((c) => c.id))
  } catch {
    myIds.value = new Set()
  }
}

async function addToMy(cap) {
  notice.value = ''
  try {
    const r = await api.post('/my/capabilities', { capability_id: cap.id })
    myIds.value = new Set([...myIds.value, cap.id])
    notice.value = r.message
  } catch (e) {
    notice.value = e.message
  }
}

async function removeFromMy(cap) {
  notice.value = ''
  try {
    const r = await api.delete(`/my/capabilities/${cap.id}`)
    const next = new Set(myIds.value)
    next.delete(cap.id)
    myIds.value = next
    notice.value = r.message
  } catch (e) {
    notice.value = e.message
  }
}

function selectBrowseTab(key) {
  const query = {}
  if (key === 'skill') query.type = 'skill'
  else if (key === 'install') query.shelf = 'install'
  else if (key === 'agent') query.type = 'agent'
  else if (key === 'more') query.type = filters.type && MORE_BROWSE_KINDS.includes(filters.type) ? filters.type : 'mcp'
  router.push({ path: '/', query })
}

function selectShelf(key) {
  // 兼容旧入口：积木→更多(mcp)，配方→助手
  if (key === 'brick') {
    selectBrowseTab('more')
    return
  }
  if (key === 'recipe') {
    selectBrowseTab('agent')
    return
  }
  const query = {}
  if (key) query.shelf = key
  router.push({ path: '/', query })
}

function goPublish(shelf = '') {
  router.push({ path: '/my', query: { publish: '1', ...(shelf ? { shelf } : {}) } })
}

function reset() {
  router.push({ path: '/' })
}

function applyFilter() {
  page.value = 1
  const query = {}
  if (filters.q) query.q = filters.q
  if (filters.type) query.type = filters.type
  else if (filters.shelf && filters.shelf !== 'all') query.shelf = filters.shelf
  else if (filters.q) query.shelf = 'all'
  if (filters.skill) query.skill = filters.skill
  if (filters.mcp) query.mcp = filters.mcp
  if (filters.sort && filters.sort !== 'latest') query.sort = filters.sort
  router.push({ path: '/', query })
}


function goPage(p) {
  if (p < 1 || p > totalPages.value) return
  page.value = p
  load()
  window.scrollTo({ top: 0 })
}

function useSort(sort) {
  filters.sort = sort
  page.value = 1
  if (showDiscovery.value) {
    // 推荐「看热门」→ 安装包货架按使用量（日常主路径）
    router.push({ path: '/', query: { shelf: 'install', sort } })
    return
  }
  load()
}

onMounted(() => {
  syncFromRoute()
  load()
  loadMy()
})

function syncFromRoute() {
  filters.skill = route.query.skill ? String(route.query.skill) : ''
  filters.mcp = route.query.mcp ? String(route.query.mcp) : ''
  filters.type = route.query.type ? String(route.query.type) : ''
  filters.shelf = route.query.shelf ? String(route.query.shelf) : ''
  filters.q = route.query.q ? String(route.query.q) : ''
  filters.sort = route.query.sort ? String(route.query.sort) : 'latest'
  if (filters.skill || filters.mcp) showAdvanced.value = true
  if (filters.type === 'skill') filters.tab = 'skill'
  else if (filters.type === 'agent') filters.tab = 'agent'
  else if (MORE_BROWSE_KINDS.includes(filters.type)) filters.tab = 'more'
  else if (filters.shelf === 'install') filters.tab = 'install'
  else filters.tab = ''
  if ((filters.skill || filters.mcp) && !filters.shelf && !filters.type) {
    filters.shelf = 'all'
  }
}

watch(
  () => route.fullPath,
  () => {
    page.value = 1
    syncFromRoute()
    load()
  }
)
</script>

<template>
  <div>
    <div class="shelf-tabs mb-16">
      <button
        v-for="t in browseTabs"
        :key="t.key || 'rec'"
        type="button"
        class="shelf-tab"
        :class="{ active: activeBrowseTab === t.key && !(filters.q && filters.shelf === 'all') }"
        :title="t.hint"
        @click="selectBrowseTab(t.key)"
      >
        {{ t.label }}
      </button>
    </div>

    <section class="hero mb-16">
      <div class="hero-main">
        <p class="hero-eyebrow">{{ pageContext.eyebrow }}</p>
        <h1 class="hero-title">{{ pageContext.title }}</h1>
        <p class="hero-desc">{{ pageContext.desc }}</p>
        <form
          v-if="showDiscovery || filters.shelf === 'all' || !!filters.q"
          class="hero-search"
          @submit.prevent="applyFilter"
        >
          <input
            v-model="filters.q"
            class="input hero-search-input"
            placeholder="搜索能力名称、描述或标签…"
            aria-label="搜索能力"
          />
          <button class="btn btn-primary" type="submit">搜索</button>
        </form>
        <div class="hero-actions">
          <button v-if="isLoggedIn" class="btn btn-primary btn-lg" type="button" @click="goPublish()">发布能力</button>
          <router-link v-else to="/login" class="btn btn-primary btn-lg">登录后发布</router-link>
          <button v-if="showDiscovery" class="btn btn-lg" type="button" @click="useSort('usage')">看热门</button>
        </div>
      </div>
      <div class="hero-stats">
        <div v-if="showDiscovery" class="hero-stat"><strong>{{ hotCaps.length || '—' }}</strong><span>近期热门</span></div>
        <div v-if="showDiscovery" class="hero-stat"><strong>{{ ratedCaps.length || '—' }}</strong><span>高分精选</span></div>
        <div v-if="showDiscovery" class="hero-stat hero-stat-link" role="button" tabindex="0" @click="selectShelf('install')" @keyup.enter="selectShelf('install')">
          <strong>安装包</strong><span>去货架浏览 →</span>
        </div>
        <div v-else class="hero-stat"><strong>{{ total }}</strong><span>当前结果</span></div>
      </div>
    </section>
    <div v-if="notice" class="alert alert-success mb-16">{{ notice }}</div>

    <div
      v-if="showDiscovery && (featuredCaps.length || hotCaps.length || ratedCaps.length)"
      class="discovery mb-16"
    >
      <section v-if="featuredCaps.length" class="discover-block">
        <div class="discover-head">
          <h3>精选推荐</h3>
          <span class="muted">默认安装或高分已上架</span>
        </div>
        <div class="grid grid-3">
          <CapabilityCard
            v-for="cap in featuredCaps"
            :key="'f-' + cap.id"
            :cap="cap"
            :in-my="myIds.has(cap.id)"
            @add="addToMy"
            @remove="removeFromMy"
          />
        </div>
      </section>
      <section v-if="hotCaps.length" class="discover-block">
        <div class="discover-head">
          <h3>近期热门</h3>
          <button class="btn btn-sm" type="button" @click="useSort('usage')">查看更多</button>
        </div>
        <div class="grid grid-3">
          <CapabilityCard
            v-for="cap in hotCaps"
            :key="'h-' + cap.id"
            :cap="cap"
            :in-my="myIds.has(cap.id)"
            @add="addToMy"
            @remove="removeFromMy"
          />
        </div>
      </section>
      <section v-if="ratedCaps.length" class="discover-block">
        <div class="discover-head">
          <h3>下载热榜 · 高分</h3>
          <button class="btn btn-sm" type="button" @click="selectShelf('install')">查看安装包</button>
        </div>
        <div class="grid grid-3">
          <CapabilityCard
            v-for="cap in ratedCaps"
            :key="'r-' + cap.id"
            :cap="cap"
            :in-my="myIds.has(cap.id)"
            @add="addToMy"
            @remove="removeFromMy"
          />
        </div>
      </section>
    </div>

    <div v-if="showDiscovery" class="discover-footer mb-16">
      <button class="btn" type="button" @click="selectBrowseTab('skill')">技能</button>
      <button class="btn" type="button" @click="selectBrowseTab('install')">安装包</button>
      <button class="btn" type="button" @click="selectBrowseTab('agent')">助手</button>
      <button class="btn" type="button" @click="selectBrowseTab('more')">更多</button>
    </div>

    <div v-if="activeBrowseTab === 'more'" class="panel toolbar mb-16">
      <span class="muted" style="font-size: 13px; margin-right: 8px">类型</span>
      <button
        v-for="k in MORE_BROWSE_KINDS"
        :key="k"
        type="button"
        class="btn btn-sm"
        :class="{ 'btn-primary': filters.type === k }"
        @click="router.push({ path: '/', query: { type: k } })"
      >
        {{ TYPE_LABELS[k] }}
      </button>
    </div>

    <template v-if="showCatalog">
      <div class="panel toolbar">
        <input
          v-model="filters.q"
          class="input"
          style="max-width: 280px"
          placeholder="搜索名称 / 描述 / 标签…"
          @keyup.enter="applyFilter"
        />
        <select v-model="filters.category" class="select" style="max-width: 150px" @change="applyFilter()">
          <option value="">业务领域</option>
          <option v-for="c in categoryOptions" :key="c" :value="c">{{ c }}</option>
        </select>
        <select v-model="filters.sort" class="select" style="max-width: 130px" @change="applyFilter()">
          <option value="latest">最新发布</option>
          <option value="usage">使用最多</option>
          <option value="rating">评分最高</option>
        </select>
        <button class="btn btn-primary" type="button" @click="applyFilter">搜索</button>
        <button class="btn" type="button" @click="reset">重置</button>
        <button class="btn btn-sm" type="button" @click="showAdvanced = !showAdvanced">
          {{ showAdvanced ? '收起高级筛选' : '高级筛选' }}
        </button>
      </div>

      <div v-if="showAdvanced" class="panel toolbar mt-8">
        <input
          v-model="filters.skill"
          class="input"
          style="max-width: 180px"
          placeholder="按 Skill 名"
          @keyup.enter="applyFilter"
        />
        <input
          v-model="filters.mcp"
          class="input"
          style="max-width: 180px"
          placeholder="按 MCP 名"
          @keyup.enter="applyFilter"
        />
        <button class="btn btn-sm btn-primary" type="button" @click="applyFilter">应用</button>
      </div>

      <div class="catalog-head mt-16 mb-8">
        <h3 style="margin: 0; font-size: 16px">{{ catalogTitle }}</h3>
        <span class="muted" style="font-size: 12px">共 {{ total }} 项</span>
      </div>

      <div v-if="loading" class="empty">加载中…</div>
      <div v-else-if="caps.length === 0" class="empty">
        没有找到符合条件的能力。
        <button
          v-if="isLoggedIn"
          class="btn btn-primary btn-sm mt-12"
          type="button"
          @click="goPublish(filters.shelf && filters.shelf !== 'all' ? filters.shelf : '')"
        >
          去发布
        </button>
      </div>
      <div v-else class="grid grid-3">
        <CapabilityCard
          v-for="cap in caps"
          :key="cap.id"
          :cap="cap"
          :in-my="myIds.has(cap.id)"
          @add="addToMy"
          @remove="removeFromMy"
        />
      </div>
      <div v-if="total > 0" class="pagination">
        <button class="btn btn-sm" type="button" :disabled="page <= 1" @click="goPage(page - 1)">上一页</button>
        <span class="muted">第 {{ page }} / {{ totalPages }} 页 · 共 {{ total }} 项</span>
        <button class="btn btn-sm" type="button" :disabled="page >= totalPages" @click="goPage(page + 1)">下一页</button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.shelf-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.shelf-tab {
  background: #fff;
  border: 1px solid var(--border);
  color: var(--muted);
  padding: 8px 14px;
  border-radius: 999px;
  cursor: pointer;
  font-size: 13px;
}
.shelf-tab:hover { color: var(--text); border-color: var(--primary); }
.shelf-tab.active {
  color: #fff;
  background: var(--primary);
  border-color: var(--primary);
}
.hero {
  display: grid;
  grid-template-columns: minmax(0, 1.55fr) minmax(220px, 0.7fr);
  gap: 24px;
  padding: 28px;
  border: 1px solid var(--border);
  border-radius: 16px;
  background:
    radial-gradient(900px 280px at 0% 0%, rgba(47, 107, 255, 0.1), transparent 55%),
    linear-gradient(180deg, #ffffff 0%, #f7f9fc 100%);
  box-shadow: var(--shadow);
}
.hero-eyebrow { margin: 0 0 8px; color: var(--primary); font-size: 13px; font-weight: 600; }
.hero-title {
  margin: 0; font-size: clamp(26px, 3vw, 34px); line-height: 1.2;
  letter-spacing: -0.02em; font-weight: 700;
}
.hero-desc { margin: 12px 0 0; color: var(--muted); font-size: 14px; line-height: 1.65; max-width: 40rem; }
.hero-search {
  display: flex;
  gap: 10px;
  margin-top: 18px;
  max-width: 520px;
}
.hero-search-input {
  flex: 1;
  min-width: 0;
  height: 42px;
  font-size: 15px;
}
.hero-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 16px; }
.btn-lg { padding: 11px 18px; font-size: 15px; font-weight: 600; }
.hero-stats { display: grid; gap: 10px; align-content: center; }
.hero-stat {
  background: #fff; border: 1px solid var(--border); border-radius: 12px;
  padding: 14px 16px; box-shadow: var(--shadow);
}
.hero-stat strong { display: block; font-size: 24px; letter-spacing: -0.02em; }
.hero-stat span { color: var(--muted); font-size: 12px; }
.hero-stat-link { cursor: pointer; transition: border-color .15s ease; }
.hero-stat-link:hover { border-color: var(--primary); }
.hero-stat-link strong { font-size: 18px; color: var(--primary); }
.discover-footer { display: flex; justify-content: center; gap: 10px; flex-wrap: wrap; }
.pagination { display: flex; align-items: center; justify-content: center; gap: 14px; margin-top: 24px; }
.discovery { display: flex; flex-direction: column; gap: 20px; }
.discover-block {
  padding: 18px 18px 8px; border: 1px solid var(--border); border-radius: 16px;
  background: #fff; box-shadow: var(--shadow);
}
.discover-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
.discover-head h3 { margin: 0; font-size: 18px; font-weight: 650; }
.catalog-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.mt-8 { margin-top: 8px; } .mt-12 { margin-top: 12px; } .mt-16 { margin-top: 16px; }
.mb-8 { margin-bottom: 8px; } .mb-16 { margin-bottom: 16px; }
@media (max-width: 860px) { .hero { grid-template-columns: 1fr; } }
</style>
