<script setup>
import { ref } from 'vue'
import { api } from '../api'

/**
 * 官方 MCP Registry 导入(仅元数据)。
 * - 搜索官方 registry（后端代理 GET /v0/servers）；
 * - 选中「导入」→ 后端归一为内部能力草稿（private/draft/binding=service，默认不可执行）；
 * - 汇入审核队列，管理员审核上架后方可执行。
 */
const keyword = ref('')
const loading = ref(false)
const importing = ref('')
const notice = ref('')
const error = ref('')
const items = ref([])
const cursor = ref('')

async function search(reset = true) {
  loading.value = true
  error.value = ''
  if (reset) {
    items.value = []
    cursor.value = ''
  }
  try {
    const qs = new URLSearchParams()
    if (keyword.value.trim()) qs.set('search', keyword.value.trim())
    qs.set('limit', '20')
    if (cursor.value) qs.set('cursor', cursor.value)
    const data = await api.get(`/admin/registry/servers?${qs.toString()}`)
    const rows = Array.isArray(data?.servers) ? data.servers : []
    items.value = reset ? rows : [...items.value, ...rows]
    cursor.value = data?.metadata?.nextCursor || ''
  } catch (e) {
    error.value = e?.message || '检索失败'
  } finally {
    loading.value = false
  }
}

async function doImport(entry) {
  const server = entry?.server || entry
  if (!server?.name) return
  importing.value = server.name
  notice.value = ''
  error.value = ''
  try {
    const cap = await api.post('/admin/registry/import', { server })
    notice.value = `已导入「${cap?.display_name || cap?.name}」为草稿（仅元数据，待审核）。`
  } catch (e) {
    error.value = e?.message || '导入失败'
  } finally {
    importing.value = ''
  }
}

function entryName(entry) {
  return entry?.server?.title || entry?.server?.name || ''
}
function entrySlug(entry) {
  return entry?.server?.name || ''
}
function entryDesc(entry) {
  return entry?.server?.description || ''
}
function entryVersion(entry) {
  return entry?.server?.version || ''
}
</script>

<template>
  <section class="panel mb-12 registry-import">
    <div class="import-head">
      <div>
        <h3 style="margin: 0">官方 MCP Registry 导入</h3>
        <div class="muted" style="font-size: 12px">
          仅入库元数据为草稿（默认不可执行）；审核上架后方可使用。来源：registry.modelcontextprotocol.io
        </div>
      </div>
      <div class="import-search">
        <input v-model="keyword" class="input" placeholder="按名称/描述搜索" @keyup.enter="search(true)" />
        <button class="btn btn-primary btn-sm" type="button" :disabled="loading" @click="search(true)">
          {{ loading ? '检索中…' : '搜索' }}
        </button>
      </div>
    </div>

    <div v-if="notice" class="alert alert-success" style="margin-top: 10px">{{ notice }}</div>
    <div v-if="error" class="alert alert-error" style="margin-top: 10px">{{ error }}</div>

    <div v-if="items.length" class="import-list">
      <div v-for="(entry, i) in items" :key="(entrySlug(entry) || '') + i" class="import-row">
        <div class="import-info">
          <div class="import-name">{{ entryName(entry) }}</div>
          <div class="muted mono" style="font-size: 12px">{{ entrySlug(entry) }} · v{{ entryVersion(entry) }}</div>
          <div class="muted import-desc">{{ entryDesc(entry) }}</div>
        </div>
        <button
          class="btn btn-sm"
          type="button"
          :disabled="importing === entrySlug(entry)"
          @click="doImport(entry)"
        >{{ importing === entrySlug(entry) ? '导入中…' : '导入' }}</button>
      </div>
      <div v-if="cursor" class="import-more">
        <button class="btn btn-sm" type="button" :disabled="loading" @click="search(false)">加载更多</button>
      </div>
    </div>
    <div v-else-if="!loading" class="muted" style="margin-top: 10px; font-size: 13px">
      输入关键词检索官方 MCP Registry（不检索其业务数据，仅获取 server.json 元数据）。
    </div>
  </section>
</template>

<style scoped>
.registry-import { padding: 16px; }
.import-head { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; flex-wrap: wrap; }
.import-search { display: flex; gap: 8px; align-items: center; }
.import-search .input { min-width: 240px; }
.import-list { margin-top: 12px; display: flex; flex-direction: column; }
.import-row {
  display: flex; justify-content: space-between; gap: 12px; align-items: flex-start;
  padding: 10px 0; border-top: 1px solid var(--border);
}
.import-row:first-child { border-top: none; }
.import-info { min-width: 0; }
.import-name { font-weight: 600; }
.import-desc {
  font-size: 12px; margin-top: 2px;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.import-more { text-align: center; padding-top: 10px; }
.mono { font-family: Consolas, monospace; }
</style>
