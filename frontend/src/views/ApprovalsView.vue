<template>
  <main class="approvals-page">
    <header class="flex-between flex-wrap">
      <div>
        <h1>审批中心</h1>
        <p class="muted small">工作流在「人工审批」节点暂停，等待你做出通过 / 驳回决定后继续执行。</p>
      </div>
      <button class="btn" type="button" :disabled="loading" @click="load">刷新</button>
    </header>

    <div v-if="error" class="error mt-12">{{ error }}</div>

    <div v-if="loading" class="muted mt-16">加载中…</div>
    <div v-else-if="!items.length" class="muted mt-16">暂无待审批项。</div>

    <div v-for="it in items" :key="it.execution_id + it.node_id" class="panel approval-item">
      <div class="flex-between flex-wrap">
        <div>
          <h3 style="margin: 0">{{ it.title || '待审批' }}</h3>
          <div class="muted small">
            {{ it.workflow_name }} · 节点 {{ it.node_id }}
            <template v-if="it.assignee"> · 指派 {{ it.assignee }}</template>
            · {{ formatDate(it.created_at) }}
          </div>
        </div>
      </div>
      <pre v-if="it.description" class="approval-desc">{{ it.description }}</pre>
      <div class="flex mt-12" style="gap: 8px; align-items: center">
        <input v-model="comments[it.execution_id + it.node_id]" class="input" placeholder="审批意见（可选）" />
        <button
          class="btn btn-success"
          type="button"
          :disabled="busy[it.execution_id + it.node_id]"
          @click="decide(it, true)"
        >通过</button>
        <button
          class="btn btn-danger"
          type="button"
          :disabled="busy[it.execution_id + it.node_id]"
          @click="decide(it, false)"
        >驳回</button>
      </div>
    </div>
  </main>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'

import { api, ApiError } from '../api'
import { formatDate } from '../utils/format'

const items = ref([])
const loading = ref(false)
const error = ref('')
const comments = reactive({})
const busy = reactive({})

function key(it) {
  return it.execution_id + it.node_id
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    items.value = await api.get('/runtime/workflows/approvals')
  } catch (err) {
    error.value = err instanceof ApiError && err.status === 403
      ? '无权查看待审批项。'
      : err?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function decide(it, approved) {
  const k = key(it)
  busy[k] = true
  error.value = ''
  try {
    await api.post(`/runtime/workflows/executions/${it.execution_id}/approve`, {
      node_id: it.node_id,
      approved,
      comment: comments[k] || ''
    })
    delete comments[k]
    await load()
  } catch (err) {
    error.value = err?.message || '操作失败'
  } finally {
    busy[k] = false
  }
}

onMounted(load)
</script>

<style scoped>
.approvals-page { max-width: 860px; margin: 0 auto; padding: 24px 20px 40px; }
.approvals-page h1 { margin: 0 0 4px; font-size: 22px; }
.small { font-size: 12px; }
.approval-item { margin-top: 14px; }
.approval-desc {
  margin: 10px 0 0;
  padding: 10px 12px;
  background: var(--panel-2, #f5f7fb);
  border-radius: 8px;
  font-size: 13px;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
}
</style>
