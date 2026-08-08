<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import { TYPE_LABELS } from '../utils/format'
import CapabilityCard from '../components/CapabilityCard.vue'

const caps = ref([])
const categories = ref({})
const loading = ref(false)
const total = ref(0)
const page = ref(1)
const pageSize = 12
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
const filters = reactive({ q: '', type: '', category: '', status: '', sort: 'latest' })

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

function reset() {
  Object.assign(filters, { q: '', type: '', category: '', status: '', sort: 'latest' })
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
})
</script>

<template>
  <div>
    <div class="panel toolbar">
      <input v-model="filters.q" class="input" style="max-width: 300px" placeholder="搜索名称 / 描述 / 标签…" @keyup.enter="load" />
      <select v-model="filters.type" class="select" style="max-width: 160px" @change="filters.category = ''; applyFilter()">
        <option value="">全部市场</option>
        <option v-for="(label, key) in TYPE_LABELS" :key="key" :value="key">{{ label }}</option>
      </select>
      <select v-model="filters.category" class="select" style="max-width: 160px" @change="applyFilter()">
        <option value="">全部分类</option>
        <option v-for="c in (categories[filters.type] || [])" :key="c" :value="c">{{ c }}</option>
      </select>
      <select v-model="filters.status" class="select" style="max-width: 130px" @change="applyFilter()">
        <option value="">全部状态</option>
        <option value="published">正式版</option>
        <option value="reviewing">待审</option>
        <option value="draft">草稿</option>
        <option value="deprecated">已弃用</option>
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
      <CapabilityCard v-for="cap in caps" :key="cap.id" :cap="cap" />
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
