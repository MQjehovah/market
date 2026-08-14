<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import { authState } from '../stores/auth'
import StatusBadge from '../components/StatusBadge.vue'

const workflows = ref([])
const loading = ref(false)
const error = ref('')
const notice = ref('')
const created = ref(null)

const canPublish = computed(() => ['admin', 'publisher'].includes(authState.user?.role))

const form = reactive({
  name: '',
  version: '0.1.0',
  description: '',
  workflowJson: `{
  "name": "示例工作流",
  "description": "多步编排",
  "version": "0.1.0",
  "on_error": "fail",
  "nodes": [
    {
      "id": "n1",
      "type": "tool",
      "capability": "glob",
      "params": { "pattern": "${'${input.pattern}'}" }
    }
  ],
  "edges": []
}`
})

async function load() {
  loading.value = true
  try {
    const body = await api.get('/capabilities?type=workflow&page_size=100')
    workflows.value = body.items
  } finally {
    loading.value = false
  }
}

async function create() {
  error.value = ''
  notice.value = ''
  created.value = null
  let workflow
  try {
    workflow = JSON.parse(form.workflowJson)
  } catch (e) {
    error.value = `workflow.json 不是合法 JSON：${e.message}`
    return
  }
  try {
    created.value = await api.post('/workflows', {
      name: form.name,
      description: form.description,
      version: form.version,
      category: '工作流',
      tags: ['工作流'],
      visibility: 'internal',
      workflow
    })
    notice.value = '工作流已创建（草稿），可提交审核发布'
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function submitReview() {
  if (!created.value) return
  try {
    created.value = await api.post(`/publish/capabilities/${created.value.id}/submit`)
    notice.value = '已提交审核'
  } catch (e) {
    error.value = e.message
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="panel">
      <h2>工作流</h2>
      <p class="muted" style="font-size: 13px">
        工作流把市场上的 tool / agent / skill / mcp 编排成多步流程；节点间用 <code>${'${nodeId.key}'}</code> 传值。
      </p>
      <div class="flex mt-8" style="gap: 10px">
        <router-link v-if="canPublish" to="/workflows/new" class="btn btn-primary">+ 新建（可视化设计器）</router-link>
      </div>
      <div v-if="error" class="alert alert-error">{{ error }}</div>
      <div v-if="notice" class="alert alert-success">{{ notice }}</div>

      <div v-if="workflows.length === 0 && !loading" class="empty">暂无工作流能力</div>
      <table v-else class="table mt-16">
        <thead>
          <tr><th>名称</th><th>版本</th><th>状态</th><th>分类</th><th>使用量</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="w in workflows" :key="w.id">
            <td>{{ w.name }}</td>
            <td>v{{ w.version }}</td>
            <td><StatusBadge :status="w.status" /></td>
            <td class="muted">{{ w.category }}</td>
            <td>{{ w.usage_count }}</td>
            <td><router-link :to="`/capabilities/${w.id}`">查看 / 执行</router-link></td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="canPublish" class="panel mt-24">
      <h3>创建工作流</h3>
      <div class="grid" style="grid-template-columns: 1fr 1fr">
        <div class="field"><label>名称</label><input v-model="form.name" class="input" placeholder="如：数据巡检与报告" /></div>
        <div class="field"><label>版本</label><input v-model="form.version" class="input" /></div>
      </div>
      <div class="field"><label>描述</label><input v-model="form.description" class="input" /></div>
      <div class="field">
        <label>workflow.json</label>
        <textarea v-model="form.workflowJson" class="textarea" rows="16" spellcheck="false"></textarea>
      </div>
      <div class="flex" style="gap: 12px">
        <button class="btn btn-primary" @click="create">创建草稿</button>
        <button v-if="created" class="btn" @click="submitReview">提交审核</button>
        <router-link v-if="created" class="btn" :to="`/capabilities/${created.id}`">查看</router-link>
      </div>
    </div>
  </div>
</template>

<style scoped>
.field { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
.field label { font-size: 13px; color: var(--muted); }
</style>
