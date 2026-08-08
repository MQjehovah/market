<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { TYPE_CATEGORIES, TYPE_LABELS } from '../utils/format'

const router = useRouter()
const error = ref('')
const form = ref({
  name: '',
  type: 'tool',
  version: '0.1.0',
  description: '',
  category: '',
  tagsText: '',
  visibility: 'internal'
})

const categories = computed(() => TYPE_CATEGORIES[form.value.type] || [])
const categoryError = computed(() => form.value.category && !categories.value.includes(form.value.category))

const packageHints = {
  agent: 'agent.json + PROMPT.md（可选 TEAM.md / tools.json / knowledge/ / skills/ / examples/）',
  tool: 'tool.json + schema.json + implementation/tool.py（可选 security.json / tests/ / examples/ / docs/）',
  skill: 'skill.json + SKILL.md（可选 templates/ / assets/ / dependencies.json / examples/）',
  mcp: 'mcp.json + connection.json + tools.json + security.json（可选 docker-compose.yml / docs/）'
}

async function submit() {
  error.value = ''
  try {
    const tags = form.value.tagsText.split(/[,，\s]+/).filter(Boolean)
    const cap = await api.post('/publish/capabilities', { ...form.value, tags })
    router.push(`/capabilities/${cap.id}`)
  } catch (e) {
    error.value = e.message
  }
}
</script>

<template>
  <div style="max-width: 760px; margin: 0 auto">
    <div class="panel">
      <h2>发布新能力</h2>
      <p class="muted">创建后为草稿状态，上传能力包（可选）并提交审核，管理员通过后上架到对应市场。</p>
      <div v-if="error" class="alert alert-error">{{ error }}</div>

      <div class="field">
        <label>能力类型</label>
        <select v-model="form.type" class="select">
          <option v-for="(label, key) in TYPE_LABELS" :key="key" :value="key">{{ label }}</option>
        </select>
      </div>
      <div class="field-row">
        <div class="field">
          <label>能力名称</label>
          <input v-model="form.name" class="input" placeholder="如：报表生成助手" />
        </div>
        <div class="field">
          <label>版本号（语义化 MAJOR.MINOR.PATCH）</label>
          <input v-model="form.version" class="input" placeholder="0.1.0" />
        </div>
      </div>
      <div class="field">
        <label>描述</label>
        <textarea v-model="form.description" class="textarea" rows="4" placeholder="说明能力的用途、边界与使用方式"></textarea>
      </div>
      <div class="field-row">
        <div class="field">
          <label>分类</label>
          <select v-model="form.category" class="select">
            <option value="">请选择分类</option>
            <option v-for="c in categories" :key="c" :value="c">{{ c }}</option>
          </select>
          <div v-if="categoryError" class="muted mt-8" style="font-size: 12px; color: var(--danger)">当前分类不在 {{ TYPE_LABELS[form.type] }} 推荐分类中</div>
        </div>
        <div class="field">
          <label>可见范围</label>
          <select v-model="form.visibility" class="select">
            <option value="private">私有（仅自己）</option>
            <option value="team">团队（同团队可见）</option>
            <option value="internal">内部（公司全员）</option>
            <option value="public">公开（经合规审批）</option>
          </select>
        </div>
      </div>
      <div class="field">
        <label>标签（逗号分隔）</label>
        <input v-model="form.tagsText" class="input" placeholder="如：数据分析, 只读, 低风险" />
      </div>

      <div class="panel" style="background: var(--panel-2); padding: 14px">
        <div class="muted" style="font-size: 13px">能力包结构要求（创建后可上传 zip 校验）</div>
        <div class="muted mt-8" style="font-size: 12px">{{ packageHints[form.type] }}</div>
      </div>

      <button class="btn btn-primary mt-24" style="width: 100%" @click="submit">创建草稿</button>
    </div>
  </div>
</template>

<style scoped>
h2 { margin-top: 0; }
</style>
