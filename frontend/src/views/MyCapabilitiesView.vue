<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { TYPE_LABELS, VISIBILITY_LABELS, formatDate } from '../utils/format'
import StatusBadge from '../components/StatusBadge.vue'

const caps = ref([])
const error = ref('')

async function load() {
  caps.value = await api.get('/publish/my')
}

async function submit(cap) {
  error.value = ''
  try {
    await api.post(`/publish/capabilities/${cap.id}/submit`)
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function remove(cap) {
  error.value = ''
  if (!confirm(`确认删除「${cap.name} v${cap.version}」？此操作不可恢复。`)) return
  try {
    await api.delete(`/publish/capabilities/${cap.id}`)
    await load()
  } catch (e) {
    error.value = e.message
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="flex-between mb-16">
      <h2 style="margin: 0">我的能力</h2>
      <router-link to="/publish" class="btn btn-primary">+ 发布新能力</router-link>
    </div>
    <div v-if="error" class="alert alert-error">{{ error }}</div>
    <div class="panel">
      <div v-if="caps.length === 0" class="empty">还没有发布过能力，点击右上角开始发布</div>
      <table v-else class="table">
        <thead>
          <tr><th>能力</th><th>市场</th><th>版本</th><th>状态</th><th>可见性</th><th>使用量</th><th>更新时间</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="cap in caps" :key="cap.id">
            <td><router-link :to="`/capabilities/${cap.id}`">{{ cap.name }}</router-link></td>
            <td>{{ TYPE_LABELS[cap.type] }}</td>
            <td>v{{ cap.version }}</td>
            <td><StatusBadge :status="cap.status" /></td>
            <td>{{ VISIBILITY_LABELS[cap.visibility] }}</td>
            <td>{{ cap.usage_count }}</td>
            <td class="muted">{{ formatDate(cap.updated_at) }}</td>
            <td>
              <div class="flex">
                <button v-if="['draft', 'returned', 'rejected'].includes(cap.status)" class="btn btn-sm btn-success" @click="submit(cap)">提交审核</button>
                <button v-if="['draft', 'returned', 'rejected'].includes(cap.status)" class="btn btn-sm btn-danger" @click="remove(cap)">删除</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
