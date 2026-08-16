<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import { authState } from '../stores/auth'
import { TYPE_LABELS } from '../utils/format'
import CapabilityCard from '../components/CapabilityCard.vue'

const caps = ref([])
const categories = ref({})
const loading = ref(false)
const total = ref(0)
const page = ref(1)
const pageSize = 12
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
const filters = reactive({ q: '', type: '', category: '', sort: 'latest' })
const myIds = ref(new Set())
const notice = ref('')

async function load() {
  loading.value = true
  try {
    const params = new URLSearchParams()
    Object.entries(filters).forEach(([k, v]) => v && params.set(k, v))
    params.set('page', page.value)
    params.set('page_size', pageSize)
    const body = await api.get(`/capabilities?${params.toString()}`)
    caps.value = body.items
    total.value = body.total
  } finally {
    loading.value = false
  }
}

async function loadCategories() {
  categories.value = await api.get('/meta/categories')
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

function reset() {
  Object.assign(filters, { q: '', type: '', category: '', sort: 'latest' })
  page.value = 1
  load()
}

function applyFilter() {
  page.value = 1
  load()
}

function goPage(p) {
  if (p < 1 || p > totalPages.value) return
  page.value = p
  load()
  window.scrollTo({ top: 0 })
}

onMounted(() => {
  load()
  loadCategories()
  loadMy()
})
</script>

<template>
  <div>
    <div class="flex-between mb-16">
      <div>
        <h2 style="margin: 0">能力市场</h2>
        <div class="muted" style="font-size: 13px">浏览全部已发布能力，点击「加入」收藏到我的能力</div>
      </div>
    </div>
    <div v-if="notice" class="alert alert-success mb-16">{{ notice }}</div>
    <div class="panel toolbar">
      <input v-model="filters.q" class="input" style="max-width: 300px" placeholder="搜索名称 / 描述 / 标签…" @keyup.enter="load" />
      <select v-model="filters.type" class="select" style="max-width: 160px" @change="filters.category = ''; applyFilter()">
        <option value="">全部类型</option>
        <option v-for="(label, key) in TYPE_LABELS" :key="key" :value="key">{{ label }}</option>
      </select>
      <select v-model="filters.category" class="select" style="max-width: 160px" @change="applyFilter()">
        <option value="">全部分类</option>
        <option v-for="c in (categories[filters.type] || [])" :key="c" :value="c">{{ c }}</option>
      </select>
      <select v-model="filters.sort" class="select" style="max-width: 130px" @change="applyFilter()">
        <option value="latest">最新发布</option>
        <option value="usage">使用最多</option>
        <option value="rating">评分最高</option>
      </select>
      <button class="btn btn-primary" @click="applyFilter">搜索</button>
      <button class="btn" @click="reset">重置</button>
    </div>

    <div v-if="loading" class="empty">加载中…</div>
    <div v-else-if="caps.length === 0" class="empty">没有找到符合条件的能力</div>
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
      <button class="btn btn-sm" :disabled="page <= 1" @click="goPage(page - 1)">上一页</button>
      <span class="muted">第 {{ page }} / {{ totalPages }} 页 · 共 {{ total }} 个能力</span>
      <button class="btn btn-sm" :disabled="page >= totalPages" @click="goPage(page + 1)">下一页</button>
    </div>
  </div>
</template>

<style scoped>
.pagination { display: flex; align-items: center; justify-content: center; gap: 14px; margin-top: 24px; }
</style>
