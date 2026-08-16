<script setup>
import { reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { TYPE_CATEGORIES, TYPE_LABELS } from '../utils/format'

const props = defineProps({ show: { type: Boolean, default: false } })
const emit = defineEmits(['close', 'created'])
const router = useRouter()

const form = reactive({
  name: '',
  type: 'tool',
  version: '0.1.0',
  description: '',
  category: '',
  tags: '',
  visibility: 'internal',
  access_policy: 'open',
  allowedUsers: '',
  workflowJson: '{\n  "nodes": [],\n  "edges": []\n}'
})
const error = ref('')
const busy = ref(false)

const categories = () => TYPE_CATEGORIES[form.type] || []

watch(
  () => props.show,
  (v) => {
    if (v) {
      error.value = ''
      busy.value = false
    }
  }
)

function selectType(type) {
  form.type = type
  form.category = ''
  error.value = ''
}

function openWorkflowEditor() {
  emit('close')
  router.push({
    path: '/workflows/new',
    query: {
      name: form.name.trim(),
      version: form.version.trim(),
      description: form.description.trim()
    }
  })
}

async function create() {
  error.value = ''
  if (!form.name.trim() || !form.version.trim()) {
    error.value = '请填写能力名称和版本号'
    return
  }
  const tags = form.tags
    .split(/[,，]/)
    .map((s) => s.trim())
    .filter(Boolean)
  const allowedUsers = form.allowedUsers
    .split(/[,，]/)
    .map((s) => s.trim())
    .filter(Boolean)
  busy.value = true
  try {
    let cap
    if (form.type === 'workflow') {
      let workflow
      try {
        workflow = JSON.parse(form.workflowJson)
      } catch (e) {
        error.value = `workflow.json 不是合法 JSON：${e.message}`
        return
      }
      cap = await api.post('/workflows', {
        name: form.name.trim(),
        description: form.description,
        version: form.version.trim(),
        category: form.category,
        tags,
        visibility: form.visibility,
        access_policy: form.access_policy,
        allowed_users: allowedUsers,
        workflow
      })
    } else {
      cap = await api.post('/publish/capabilities', {
        name: form.name.trim(),
        description: form.description,
        type: form.type,
        version: form.version.trim(),
        category: form.category,
        tags,
        visibility: form.visibility,
        access_policy: form.access_policy,
        allowed_users: allowedUsers
      })
    }
    emit('created', cap)
    router.push(`/capabilities/${cap.id}`)
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div v-if="show" class="modal-mask" @click.self="emit('close')">
    <div class="modal panel">
      <div class="modal-header">
        <h3 style="margin: 0">新建能力</h3>
        <button class="modal-close" @click="emit('close')">✕</button>
      </div>

      <div class="grid" style="grid-template-columns: 1fr 1fr">
        <div class="field">
          <label>名称</label>
          <input v-model="form.name" class="input" placeholder="如：文件哈希计算" />
        </div>
        <div class="field">
          <label>版本（语义化）</label>
          <input v-model="form.version" class="input" placeholder="0.1.0" />
        </div>
      </div>

      <div class="field mt-12">
        <label>类型</label>
        <select v-model="form.type" class="select" @change="selectType(form.type)">
          <option v-for="(label, key) in TYPE_LABELS" :key="key" :value="key">{{ label }}</option>
        </select>
      </div>

      <div class="field mt-12">
        <label>描述</label>
        <textarea v-model="form.description" class="textarea" rows="3" placeholder="简要介绍这个能力做什么"></textarea>
      </div>

      <div class="grid mt-12" style="grid-template-columns: 1fr 1fr">
        <div class="field">
          <label>分类</label>
          <select v-model="form.category" class="select">
            <option value="">不选</option>
            <option v-for="c in categories()" :key="c" :value="c">{{ c }}</option>
          </select>
        </div>
        <div class="field">
          <label>可见性</label>
          <select v-model="form.visibility" class="select">
            <option value="internal">内部（全员可见）</option>
            <option value="public">公开</option>
            <option value="team">团队</option>
            <option value="private">私有（仅自己）</option>
          </select>
        </div>
      </div>

      <div class="grid mt-12" style="grid-template-columns: 1fr 1fr">
        <div class="field">
          <label>调用权限</label>
          <select v-model="form.access_policy" class="select">
            <option value="open">开放：所有登录用户可加入并调用</option>
            <option value="admin_only">仅管理员：普通账号不可调用</option>
            <option value="restricted">白名单：仅指定用户可调用</option>
          </select>
        </div>
        <div v-if="form.access_policy === 'restricted'" class="field">
          <label>白名单用户名（逗号分隔）</label>
          <input v-model="form.allowedUsers" class="input" placeholder="如：zhangsan, lisi" />
        </div>
      </div>

      <div class="field mt-12">
        <label>标签（逗号分隔）</label>
        <input v-model="form.tags" class="input" placeholder="如：报表, 数据分析" />
      </div>

      <div v-if="form.type === 'workflow'" class="field mt-12">
        <label>工作流是能力的一种，推荐使用可视化编辑器（类似 Dify 画布）</label>
        <button class="btn btn-primary" style="width: 100%" @click="openWorkflowEditor">
          🎨 打开可视化编辑器
        </button>
        <details class="mt-8">
          <summary class="muted" style="cursor: pointer; font-size: 12px">
            高级：直接粘贴 workflow.json（节点引用 tool / agent / skill / mcp）
          </summary>
          <textarea
            v-model="form.workflowJson"
            class="textarea mt-8"
            rows="8"
            spellcheck="false"
          ></textarea>
        </details>
      </div>

      <div v-if="error" class="alert alert-error mt-12">{{ error }}</div>

      <div class="modal-foot">
        <span class="muted" style="font-size: 12px">
          创建后为草稿，可在详情页上传能力包并提交审核（审核由管理员完成）
        </span>
        <div class="flex" style="gap: 10px">
          <button class="btn" @click="emit('close')">取消</button>
          <button class="btn btn-primary" :disabled="busy" @click="create">
            {{ busy ? '创建中…' : '创建草稿' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-mask {
  position: fixed; inset: 0; background: rgba(5, 8, 16, 0.72); z-index: 100;
  display: flex; align-items: center; justify-content: center; padding: 20px;
}
.modal { width: 640px; max-width: 100%; max-height: 90vh; overflow: auto; box-shadow: 0 24px 64px rgba(0,0,0,.55); }
.modal-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.modal-close { background: none; border: none; color: var(--muted); font-size: 16px; cursor: pointer; }
.modal-close:hover { color: var(--text); }
.field { display: flex; flex-direction: column; gap: 6px; }
.field label { font-size: 13px; color: var(--muted); }
.mt-12 { margin-top: 12px; }
.modal-foot { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-top: 20px; }
</style>
