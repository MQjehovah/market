<script setup>
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import { formatSize } from '../utils/format'
import StatusBadge from '../components/StatusBadge.vue'
import MarkdownEditor from '../components/MarkdownEditor.vue'

const route = useRoute()
const name = route.params.name

const data = ref(null)
const skillMd = ref('')
const description = ref('')
const tags = ref('')
const error = ref('')
const notice = ref('')
const saving = ref(false)

async function load() {
  error.value = ''
  try {
    data.value = await api.get(`/skills/${encodeURIComponent(name)}/edit`)
    skillMd.value = data.value.skill_md || ''
    description.value = data.value.capability?.description || ''
    tags.value = (data.value.capability?.tags || []).join(', ')
  } catch (e) {
    error.value = e.message
  }
}

async function save() {
  error.value = ''
  notice.value = ''
  saving.value = true
  try {
    const body = await api.put(`/skills/${encodeURIComponent(name)}/edit`, {
      skill_md: skillMd.value,
      description: description.value,
      category: data.value?.capability?.category || '',
      tags: tags.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
    })
    data.value = body
    skillMd.value = body.skill_md || ''
    notice.value = `已保存为新版本草稿 v${body.capability.version}，可提交审核`
  } catch (e) {
    error.value = e.message
  } finally {
    saving.value = false
  }
}

async function submitReview() {
  error.value = ''
  try {
    const cap = await api.post(`/publish/capabilities/${data.value.capability.id}/submit`)
    notice.value = '已提交审核'
    data.value.capability = cap
    await load()
  } catch (e) {
    error.value = e.message
  }
}

onMounted(load)
</script>

<template>
  <div v-if="error && !data" class="empty">{{ error }}</div>
  <div v-else-if="data" class="editor">
    <div class="flex-between mb-16">
      <div>
        <h2 style="margin: 0">编辑技能：{{ data.capability.name }}</h2>
        <div class="muted" style="font-size: 13px">
          <StatusBadge :status="data.capability.status" />
          当前编辑版本 v{{ data.capability.version }}
          <span v-if="data.base_version"> · 基线版本 v{{ data.base_version }}</span>
          · 保存即生成新版本草稿，附属文件（references/scripts/assets）自动保留
        </div>
      </div>
      <div class="flex" style="gap: 10px">
        <router-link :to="`/capabilities/${data.capability.id}`" class="btn">查看详情</router-link>
        <button class="btn btn-primary" :disabled="saving" @click="save">{{ saving ? '保存中…' : '保存为新版本' }}</button>
      </div>
    </div>

    <div v-if="error" class="alert alert-error mb-16">{{ error }}</div>
    <div v-if="notice" class="alert alert-success mb-16">{{ notice }}</div>

    <div class="grid" style="grid-template-columns: 1fr 320px; align-items: start">
      <div>
        <div class="panel">
          <h3>SKILL.md（Markdown）</h3>
          <MarkdownEditor v-model="skillMd" />
        </div>

        <div class="panel mt-16">
          <h3>描述</h3>
          <textarea v-model="description" class="textarea" rows="3"></textarea>
        </div>

        <div class="panel mt-16">
          <h3>标签（逗号分隔）</h3>
          <input v-model="tags" class="input" placeholder="如：数据分析, 报表" />
        </div>

        <div class="panel mt-16 flex-between">
          <span class="muted" style="font-size: 13px">保存后草稿需要提交审核，管理员通过后才会成为正式版</span>
          <button v-if="['draft', 'returned', 'rejected'].includes(data.capability.status)" class="btn btn-success" @click="submitReview">提交审核</button>
        </div>
      </div>

      <div class="panel">
        <h3>附属文件（自动保留）</h3>
        <div v-if="data.files.length === 0" class="muted" style="font-size: 13px">无附属文件</div>
        <div v-for="f in data.files" :key="f.path" class="file-item">
          <code>{{ f.path }}</code>
          <span class="muted" style="font-size: 12px">{{ formatSize(f.size) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.editor { max-width: 1520px; }
h3 { margin: 0 0 12px; }
.file-item {
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 13px;
}
.file-item:last-child { border-bottom: none; }
.file-item code { word-break: break-all; }
</style>
