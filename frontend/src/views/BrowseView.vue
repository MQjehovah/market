<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { authState } from '../stores/auth'
import {
  DEFAULT_BROWSE_KINDS,
  JOIN_VS_INSTALL_HINT,
  MORE_BROWSE_KINDS,
  SHELVES,
  TYPE_CATEGORIES,
  TYPE_LABELS,
  isLocalInstallKind
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
const taskResults = ref({ agents: [], skills: [], mcps: [], plugins: [], others: [] })
const loading = ref(false)
const total = ref(0)
const page = ref(1)
const pageSize = 12
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))

const emptyTaskGroups = () => ({ agents: [], skills: [], mcps: [], plugins: [], others: [] })

const taskSections = [
  { key: 'agents', title: '推荐专家', hint: '场景级问答；依赖技能/连接器会一并带出' },
  { key: 'skills', title: '相关技能', hint: '问答 SOP，装进专家后提问即可按流程回答' },
  { key: 'mcps', title: '相关连接器', hint: '给专家接外部系统' }
]

const filters = reactive({
  q: '',
  shelf: '',
  type: '',
  category: '',
  tag: '',
  skill: '',
  mcp: '',
  sort: 'latest',
  tab: '' // '', agent, skill, mcp, more
})
const myIds = ref(new Set())
const tagOptions = ref([])
const notice = ref('')
const noticeHref = ref('')

const browseTabs = [
  { key: '', label: '推荐', hint: '专家与依赖（技能 / 连接器）的精选与热门' },
  { key: 'agent', label: '专家', hint: '场景级问答专家；依赖随专家生效' },
  { key: 'skill', label: '技能', hint: '问答 SOP；装进专家后提问即可按该流程回答' },
  { key: 'mcp', label: '连接器', hint: '给专家接外部系统' },
  { key: 'more', label: '更多', hint: '能力编排与编排函数' }
]

/** 推荐页：无货架、无类型、无搜索、无其它筛选 */
const showDiscovery = computed(
  () =>
    !filters.q &&
    !filters.shelf &&
    !filters.type &&
    !filters.tab &&
    !filters.category &&
    !filters.tag &&
    !filters.skill &&
    !filters.mcp &&
    filters.sort === 'latest' &&
    page.value === 1
)

/** 有关键词时走「按要办的事」分组结果，不再用普通分页列表 */
const showTaskSearch = computed(() => Boolean(String(filters.q || '').trim()))

const showCatalog = computed(() => !showDiscovery.value && !showTaskSearch.value)

const taskHitCount = computed(() => {
  const t = taskResults.value
  return (
    (t.agents?.length || 0) +
    (t.skills?.length || 0) +
    (t.mcps?.length || 0) +
    (t.others?.length || 0)
  )
})

const taskEmpty = computed(() => showTaskSearch.value && !loading.value && taskHitCount.value === 0)

const pageContext = computed(() => {
  if (filters.q) {
    return {
      eyebrow: '任务匹配',
      title: '为你找到这些能力',
      desc: '按你要办的事匹配，并顺着依赖带出配套专家、技能与连接器。'
    }
  }
  if (filters.tab === 'skill' || filters.type === 'skill') {
    return { eyebrow: '目录', title: '技能', desc: '问答 SOP；加入后装进专家，在对话里提问即可按该流程回答。' }
  }
  if (filters.tab === 'mcp' || filters.type === 'mcp') {
    return { eyebrow: '目录', title: '连接器', desc: '给专家接外部系统；连上后发现的工具才可调。' }
  }
  if (filters.tab === 'agent' || filters.type === 'agent') {
    return { eyebrow: '目录', title: '专家', desc: '场景级问答专家；依赖的技能/连接器会随专家一起生效。' }
  }
  if (filters.tab === 'more' || MORE_BROWSE_KINDS.includes(filters.type)) {
    return {
      eyebrow: '目录',
      title: '更多',
      desc: '能力编排与编排函数等进阶类型。'
    }
  }
  if (filters.shelf && SHELVES[filters.shelf]) {
    const s = SHELVES[filters.shelf]
    return { eyebrow: '分类', title: s.label, desc: s.description }
  }
  if (filters.shelf === 'all') {
    return { eyebrow: '能力总览', title: '搜索结果', desc: '含专家、技能、连接器与更多类型。' }
  }
  if (filters.sort === 'usage') {
    return { eyebrow: '目录', title: '近期热门', desc: '按使用次数排列。范围仍是当前目录。' }
  }
  if (filters.sort === 'rating') {
    return { eyebrow: '目录', title: '高分能力', desc: '按评分排列。范围仍是当前目录。' }
  }
  return {
    eyebrow: '企业内部的 AI 能力',
    title: '发现当下值得使用的能力',
    desc: JOIN_VS_INSTALL_HINT
  }
})

const catalogTitle = computed(() => {
  if (filters.q) return '搜索结果'
  if (filters.tag) return `标签：${filters.tag}`
  if (filters.sort === 'usage' && !filters.type && !(filters.shelf && SHELVES[filters.shelf])) return '近期热门'
  if (filters.sort === 'rating' && !filters.type && !(filters.shelf && SHELVES[filters.shelf])) return '高分能力'
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
  if (filters.type === 'mcp') return 'mcp'
  if (MORE_BROWSE_KINDS.includes(filters.type)) return 'more'
  if (filters.shelf === 'all') return ''
  return ''
})

async function fetchList(extra = {}) {
  const params = new URLSearchParams()
  if (filters.q) params.set('q', filters.q)
  if (filters.type) {
    params.set('type', filters.type)
  } else if (filters.shelf === 'all' || filters.tag) {
    params.set('include_bricks', 'true')
  } else if (filters.shelf) {
    params.set('shelf', filters.shelf)
  }
  if (filters.category) params.set('category', filters.category)
  if (filters.tag) params.set('tag', filters.tag)
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
    const seen = new Set()
    const pool = [...(rated.items || []), ...(hot.items || [])]
    let featured = pool
      .filter((c) => {
        if (seen.has(c.id)) return false
        seen.add(c.id)
        return (Number(c.avg_rating) || 0) >= 4
      })
      .slice(0, 6)
    if (featured.length < 3) featured = (rated.items || []).slice(0, 6)
    const featuredIds = new Set(featured.map((c) => c.id))
    const hotList = (hot.items || []).filter((c) => !featuredIds.has(c.id))
    const hotIds = new Set(hotList.map((c) => c.id))
    featuredCaps.value = featured
    hotCaps.value = hotList
    ratedCaps.value = (rated.items || []).filter((c) => !featuredIds.has(c.id) && !hotIds.has(c.id))
  } catch {
    hotCaps.value = []
    ratedCaps.value = []
    featuredCaps.value = []
  }
}

async function loadTaskSearch() {
  const q = String(filters.q || '').trim()
  const body = await api.get(`/capabilities/task-search?q=${encodeURIComponent(q)}`)
  const next = {
    agents: body.agents || [],
    skills: body.skills || [],
    mcps: body.mcps || [],
    others: body.others || []
  }
  // 安装包保留检索能力但不展示分组
  taskResults.value = next
  total.value = next.agents.length + next.skills.length + next.mcps.length + next.others.length
  caps.value = []
  hotCaps.value = []
  ratedCaps.value = []
  featuredCaps.value = []
}

async function load() {
  loading.value = true
  try {
    if (showDiscovery.value) {
      caps.value = []
      total.value = 0
      taskResults.value = emptyTaskGroups()
      await loadDiscovery()
      return
    }
    if (showTaskSearch.value) {
      await loadTaskSearch()
      return
    }
    taskResults.value = emptyTaskGroups()
    const body = await fetchList()
    caps.value = body.items
    total.value = body.total
    hotCaps.value = []
    ratedCaps.value = []
    featuredCaps.value = []
  } catch (e) {
    caps.value = []
    total.value = 0
    taskResults.value = emptyTaskGroups()
    notice.value = e.message || '加载目录失败'
  } finally {
    loading.value = false
  }
}

async function loadMy() {
  if (!authState.token) return
  try {
    const items = await api.get('/my/capabilities?scope=added')
    myIds.value = new Set(items.map((c) => c.id))
  } catch {
    myIds.value = new Set()
  }
}

async function loadTagOptions() {
  try {
    const body = await api.get('/meta/tags')
    tagOptions.value = body?.tags || []
  } catch {
    tagOptions.value = []
  }
}

async function addToMy(cap) {
  notice.value = ''
  noticeHref.value = ''
  try {
    const r = await api.post('/my/capabilities', { capability_id: cap.id })
    myIds.value = new Set([...myIds.value, cap.id])
    noticeHref.value = `/capabilities/${cap.id}`
    if (cap.distribution === 'remote') {
      const extra = r?.message && r.message.includes('未加入') ? `；${r.message}` : ''
      notice.value = `已加入「${cap.name}」。云端能力加入即用，无需安装${extra}`
    } else if (r?.message) {
      notice.value = r.message
    } else if (isLocalInstallKind(cap.type)) {
      notice.value = `已加入「${cap.name}」。可打开详情本地安装，或在零号员工 / 桌面中安装使用。`
    } else if (cap.type === 'agent' || cap.type === 'mcp') {
      notice.value = `已加入「${cap.name}」。下一步：打开详情试用，或在零号员工 / 桌面中安装使用。`
    } else {
      notice.value = `已加入「${cap.name}」。`
    }
  } catch (e) {
    notice.value = e.message
  }
}

function selectBrowseTab(key) {
  const query = {}
  if (key === 'skill') query.type = 'skill'
  else if (key === 'mcp') query.type = 'mcp'
  else if (key === 'agent') query.type = 'agent'
  else if (key === 'more') {
    query.type =
      filters.type && MORE_BROWSE_KINDS.includes(filters.type) ? filters.type : 'workflow'
  }
  router.push({ path: '/', query })
}

function selectShelf(key) {
  // 兼容旧入口：积木→技能，配方→专家，安装包→专家（主叙事）
  if (key === 'brick') {
    selectBrowseTab('skill')
    return
  }
  if (key === 'recipe' || key === 'install') {
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

function catalogQuery({ page: p = page.value, sort = filters.sort } = {}) {
  const query = {}
  if (filters.q) query.q = filters.q
  if (filters.type) query.type = filters.type
  else if (filters.shelf && filters.shelf !== 'all') query.shelf = filters.shelf
  else if (filters.q) query.shelf = 'all'
  if (filters.category) query.category = filters.category
  if (filters.tag) query.tag = filters.tag
  if (filters.skill) query.skill = filters.skill
  if (filters.mcp) query.mcp = filters.mcp
  if (sort && sort !== 'latest') query.sort = sort
  if (p > 1) query.page = String(p)
  return query
}

function applyFilter() {
  page.value = 1
  router.push({ path: '/', query: catalogQuery({ page: 1 }) })
}

function goPage(p) {
  if (p < 1 || p > totalPages.value) return
  router.push({ path: '/', query: catalogQuery({ page: p }) })
  window.scrollTo({ top: 0 })
}

function useSort(sort) {
  filters.sort = sort
  page.value = 1
  router.push({ path: '/', query: catalogQuery({ page: 1, sort }) })
}

onMounted(() => {
  syncFromRoute()
  load()
  loadMy()
  loadTagOptions()
})

function syncFromRoute() {
  filters.skill = route.query.skill ? String(route.query.skill) : ''
  filters.mcp = route.query.mcp ? String(route.query.mcp) : ''
  filters.type = route.query.type ? String(route.query.type) : ''
  filters.shelf = route.query.shelf ? String(route.query.shelf) : ''
  filters.q = route.query.q ? String(route.query.q) : ''
  filters.category = route.query.category ? String(route.query.category) : ''
  filters.tag = route.query.tag ? String(route.query.tag) : ''
  filters.sort = route.query.sort ? String(route.query.sort) : 'latest'
  const rawPage = Number(route.query.page)
  page.value = Number.isFinite(rawPage) && rawPage > 0 ? Math.floor(rawPage) : 1
  if (filters.skill || filters.mcp) showAdvanced.value = true
  if (filters.type === 'skill') filters.tab = 'skill'
  else if (filters.type === 'agent') filters.tab = 'agent'
  else if (filters.type === 'mcp') filters.tab = 'mcp'
  else if (MORE_BROWSE_KINDS.includes(filters.type)) filters.tab = 'more'
  else if (filters.shelf === 'install') filters.tab = 'agent' // 旧链接落到专家
  else filters.tab = ''
  if ((filters.skill || filters.mcp) && !filters.shelf && !filters.type) {
    filters.shelf = 'all'
  }
}

watch(
  () => route.fullPath,
  () => {
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
            placeholder="想办什么事？例如：处理工单、查知识库"
            aria-label="按要办的事搜索"
          />
          <button class="btn btn-primary" type="submit">搜索</button>
        </form>
        <div class="hero-actions">
          <button v-if="showDiscovery" class="btn btn-primary btn-lg" type="button" @click="useSort('usage')">看热门</button>
          <button v-if="isLoggedIn" class="btn btn-lg" :class="{ 'btn-primary': !showDiscovery }" type="button" @click="goPublish()">发布能力</button>
          <router-link v-else to="/login" class="btn btn-lg" :class="{ 'btn-primary': !showDiscovery }">登录后发布</router-link>
        </div>
      </div>
      <div class="hero-stats">
        <div v-if="showDiscovery" class="hero-stat"><strong>{{ hotCaps.length || '—' }}</strong><span>近期热门</span></div>
        <div v-if="showDiscovery" class="hero-stat"><strong>{{ ratedCaps.length || '—' }}</strong><span>高分精选</span></div>
        <div v-if="!showDiscovery" class="hero-stat"><strong>{{ total }}</strong><span>{{ showTaskSearch ? '匹配能力' : '当前结果' }}</span></div>
      </div>
    </section>
    <div v-if="notice" class="alert alert-success mb-16">
      {{ notice }}
      <router-link v-if="noticeHref" :to="noticeHref" style="margin-left: 8px">查看详情</router-link>
    </div>

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
          />
        </div>
      </section>
      <section v-if="ratedCaps.length" class="discover-block">
        <div class="discover-head">
          <h3>下载热榜 · 高分</h3>
          <button class="btn btn-sm" type="button" @click="useSort('rating')">查看更多</button>
        </div>
        <div class="grid grid-3">
          <CapabilityCard
            v-for="cap in ratedCaps"
            :key="'r-' + cap.id"
            :cap="cap"
            :in-my="myIds.has(cap.id)"
            @add="addToMy"
          />
        </div>
      </section>
    </div>

    <div v-if="showDiscovery" class="discover-footer mb-16">
      <button class="btn" type="button" @click="selectBrowseTab('agent')">专家</button>
      <button class="btn" type="button" @click="selectBrowseTab('skill')">技能</button>
      <button class="btn" type="button" @click="selectBrowseTab('mcp')">连接器</button>
      <button class="btn" type="button" @click="selectBrowseTab('more')">更多</button>
    </div>

    <div v-if="showTaskSearch" class="task-search mb-16">
      <div v-if="loading" class="muted" style="padding: 24px 0">正在按任务匹配…</div>
      <template v-else-if="taskEmpty">
        <div class="panel" style="padding: 28px 24px; text-align: center">
          <p style="margin: 0 0 8px; font-size: 15px">没找到相关能力</p>
          <p class="muted" style="margin: 0 0 16px; font-size: 13px">换个说法试试，或直接去逛技能 / 专家目录。</p>
          <div style="display: flex; gap: 8px; justify-content: center; flex-wrap: wrap">
            <button class="btn btn-primary" type="button" @click="selectBrowseTab('skill')">去逛技能</button>
            <button class="btn" type="button" @click="selectBrowseTab('agent')">去逛专家</button>
            <button class="btn" type="button" @click="router.push({ path: '/', query: { type: 'mcp' } })">去逛连接器</button>
          </div>
        </div>
      </template>
      <template v-else>
        <section
          v-for="sec in taskSections"
          v-show="(taskResults[sec.key] || []).length"
          :key="sec.key"
          class="discover-block"
        >
          <div class="discover-head">
            <h3>{{ sec.title }}</h3>
            <span class="muted">{{ sec.hint }}</span>
          </div>
          <div class="grid grid-3">
            <CapabilityCard
              v-for="cap in taskResults[sec.key]"
              :key="sec.key + '-' + cap.id"
              :cap="cap"
              :in-my="myIds.has(cap.id)"
              @add="addToMy"
            />
          </div>
        </section>
        <section v-if="(taskResults.others || []).length" class="discover-block">
          <div class="discover-head">
            <h3>其它</h3>
            <span class="muted">规则、命令、Hooks 等</span>
          </div>
          <div class="grid grid-3">
            <CapabilityCard
              v-for="cap in taskResults.others"
              :key="'o-' + cap.id"
              :cap="cap"
              :in-my="myIds.has(cap.id)"
              @add="addToMy"
            />
          </div>
        </section>
      </template>
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
          placeholder="想办什么事？例如：处理工单、查知识库"
          @keyup.enter="applyFilter"
        />
        <select v-model="filters.category" class="select" style="max-width: 150px" @change="applyFilter()">
          <option value="">业务领域</option>
          <option v-for="c in categoryOptions" :key="c" :value="c">{{ c }}</option>
        </select>
        <select v-model="filters.tag" class="select" style="max-width: 150px" @change="applyFilter()">
          <option value="">标签</option>
          <option v-for="t in tagOptions" :key="t" :value="t">{{ t }}</option>
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
          placeholder="按连接器名"
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
  gap: 20px;
  padding: 20px 24px;
  border: 1px solid var(--border);
  border-radius: 16px;
  background:
    radial-gradient(900px 280px at 0% 0%, rgba(47, 107, 255, 0.1), transparent 55%),
    linear-gradient(180deg, #ffffff 0%, #f7f9fc 100%);
  box-shadow: var(--shadow);
}
.hero-eyebrow { margin: 0 0 6px; color: var(--primary); font-size: 13px; font-weight: 600; }
.hero-title {
  margin: 0; font-size: clamp(23px, 2.6vw, 29px); line-height: 1.2;
  letter-spacing: -0.02em; font-weight: 700;
}
.hero-desc { margin: 8px 0 0; color: var(--muted); font-size: 14px; line-height: 1.55; max-width: 40rem; }
.hero-search {
  display: flex;
  gap: 10px;
  margin-top: 14px;
  max-width: 520px;
}
.hero-search-input {
  flex: 1;
  min-width: 0;
  height: 40px;
  font-size: 15px;
}
.hero-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 12px; }
.btn-lg { padding: 9px 16px; font-size: 15px; font-weight: 600; }
.hero-stats { display: grid; gap: 8px; align-content: center; }
.hero-stat {
  background: #fff; border: 1px solid var(--border); border-radius: 12px;
  padding: 10px 14px; box-shadow: var(--shadow);
}
.hero-stat strong { display: block; font-size: 20px; letter-spacing: -0.02em; }
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
