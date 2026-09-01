<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import {
  KIND_HINTS,
  ORCH_LABELS,
  PACKAGE_HINTS,
  PUBLISH_INTENTS,
  REVIEW_CHECKLIST,
  SHELVES,
  TYPE_CATEGORIES,
  TYPE_LABELS,
  needsZipUpload,
  nextRouteAfterCreate
} from '../utils/format'

const props = defineProps({
  show: { type: Boolean, default: false },
  initialShelf: { type: String, default: '' }
})
const emit = defineEmits(['close', 'created'])
const router = useRouter()

const step = ref('intent')
const form = reactive({
  shelf: 'install',
  name: '',
  type: 'plugin',
  version: '0.1.0',
  description: '',
  category: '',
  tags: '',
  visibility: 'internal',
  access_policy: 'open',
  install_policy: 'optional',
  allowedUsers: '',
  workflowJson: '{\n  "nodes": [],\n  "edges": []\n}'
})
const error = ref('')
const busy = ref(false)

const shelfList = computed(() => Object.values(SHELVES))
const kindsInShelf = computed(() => SHELVES[form.shelf]?.kinds || [])
const kindHint = computed(() => KIND_HINTS[form.type] || null)
const showInstallPolicy = computed(() => ['plugin', 'agent', 'mcp'].includes(form.type))
const footHint = computed(() => {
  if (form.type === 'workflow') return '创建后进入 Workflow 画布；无需上传 zip'
  if (form.type === 'plugin') return '创建后请在详情页上传 plugin zip；审核通过后可一键加入'
  if (needsZipUpload(form.type)) return '创建后为草稿，详情页上传能力包并提交审核'
  return '创建后为草稿，完善内容后提交审核'
})

watch(
  () => props.show,
  (v) => {
    if (!v) return
    error.value = ''
    busy.value = false
    step.value = 'intent'
    if (props.initialShelf && SHELVES[props.initialShelf]) {
      selectIntent(props.initialShelf)
    }
  }
)

function categories() {
  return TYPE_CATEGORIES[form.type] || []
}

function selectIntent(key) {
  const intent = PUBLISH_INTENTS.find((i) => i.key === key)
  form.shelf = key
  form.type = intent?.defaultType || SHELVES[key]?.kinds?.[0] || 'plugin'
  form.category = ''
  error.value = ''
  step.value = 'form'
}

function selectShelf(key) {
  form.shelf = key
  if (key === 'brick') form.type = 'skill'
  else if (key === 'recipe') form.type = 'agent'
  else form.type = SHELVES[key]?.kinds?.[0] || 'plugin'
  form.category = ''
  error.value = ''
}

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
    const common = {
      name: form.name.trim(),
      description: form.description,
      version: form.version.trim(),
      category: form.category,
      tags,
      visibility: form.visibility,
      access_policy: form.access_policy,
      allowed_users: allowedUsers,
      install_policy: showInstallPolicy.value ? form.install_policy : 'optional'
    }
    if (form.type === 'workflow') {
      let workflow
      try {
        workflow = JSON.parse(form.workflowJson)
      } catch (e) {
        error.value = `workflow.json 不是合法 JSON：${e.message}`
        return
      }
      cap = await api.post('/workflows', { ...common, workflow })
    } else {
      cap = await api.post('/publish/capabilities', { ...common, type: form.type })
    }
    emit('created', cap)
    emit('close')
    router.push(nextRouteAfterCreate(cap))
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
        <h3 style="margin: 0">{{ step === 'intent' ? '发布能力：你想做什么？' : '填写能力信息' }}</h3>
        <button class="modal-close" type="button" @click="emit('close')">✕</button>
      </div>

      <template v-if="step === 'intent'">
        <div class="muted" style="font-size: 13px; line-height: 1.5; margin-bottom: 12px">
          本站负责能力沉淀与分发；执行在零号员工 / IDE。
          想一次装齐场景能力？优先选「发安装包」。
        </div>
        <div class="intent-grid">
          <button
            v-for="intent in PUBLISH_INTENTS"
            :key="intent.key"
            type="button"
            class="intent-card"
            @click="selectIntent(intent.key)"
          >
            <strong>{{ intent.label }}</strong>
            <span>{{ intent.blurb }}</span>
          </button>
        </div>
        <div class="modal-foot">
          <span class="muted" style="font-size: 12px">也可稍后在表单里切换货架</span>
          <button class="btn" type="button" @click="emit('close')">取消</button>
        </div>
      </template>

      <template v-else>
        <button class="btn btn-sm mb-12" type="button" @click="step = 'intent'">← 重选意图</button>

        <div class="field">
          <label>货架</label>
          <div class="shelf-grid">
            <button
              v-for="s in shelfList"
              :key="s.key"
              type="button"
              class="shelf-card"
              :class="{ active: form.shelf === s.key }"
              @click="selectShelf(s.key)"
            >
              <strong>{{ s.label }}</strong>
              <span>{{ s.description }}</span>
            </button>
          </div>
        </div>

        <div class="field mt-12">
          <label>类型</label>
          <div class="kind-row">
            <button
              v-for="k in kindsInShelf"
              :key="k"
              type="button"
              class="kind-chip"
              :class="{ active: form.type === k }"
              @click="selectType(k)"
            >
              {{ TYPE_LABELS[k] }}
              <em v-if="k === 'tool'">（编排节点）</em>
            </button>
          </div>
          <div v-if="kindHint" class="kind-hint muted">
            <div><strong>是什么：</strong>{{ kindHint.what }}</div>
            <div><strong>装到哪：</strong>{{ kindHint.where }}</div>
            <div><strong>谁执行：</strong>{{ kindHint.whoRuns }}</div>
            <div class="mt-8"><strong>包结构：</strong>{{ PACKAGE_HINTS[form.type] }}</div>
          </div>
        </div>

        <div v-if="form.type === 'agent'" class="alert mt-12" style="font-size: 13px">
          有 <code>TEAM.md</code> 时为<strong>{{ ORCH_LABELS.team.name }}</strong>（角色协作，在零号员工执行）。
          不要用能力编排 Workflow 去替代 TEAM.md。
          依赖的 skill / mcp 需先上架；若想一次内嵌，请改发「安装包」。
        </div>
        <div v-if="form.type === 'workflow'" class="alert mt-12" style="font-size: 13px">
          <strong>{{ ORCH_LABELS.capability.name }}</strong>：节点是已上架能力，只在云端执行，不进 Agent 目录。
          与 TEAM.md <strong>平行</strong>，禁止节点互转。
        </div>

        <div class="grid mt-12" style="grid-template-columns: 1fr 1fr">
          <div class="field">
            <label>名称</label>
            <input v-model="form.name" class="input" placeholder="全局唯一逻辑名" />
          </div>
          <div class="field">
            <label>版本（语义化）</label>
            <input v-model="form.version" class="input" placeholder="0.1.0" />
          </div>
        </div>

        <div class="field mt-12">
          <label>描述</label>
          <textarea
            v-model="form.description"
            class="textarea"
            rows="3"
            placeholder="这个能力做什么、给谁用"
          ></textarea>
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
            <label>可见范围（治理）</label>
            <select v-model="form.visibility" class="select">
              <option value="private">仅自己（草稿协作）</option>
              <option value="team">部分用户 / 团队</option>
              <option value="internal">企业内部（全员可见）</option>
              <option value="public">更大范围（公开）</option>
            </select>
          </div>
        </div>

        <div class="grid mt-12" style="grid-template-columns: 1fr 1fr">
          <div class="field">
            <label>调用权限</label>
            <select v-model="form.access_policy" class="select">
              <option value="open">开放：登录用户可加入并调用</option>
              <option value="admin_only">仅管理员</option>
              <option value="restricted">白名单</option>
            </select>
          </div>
          <div v-if="form.access_policy === 'restricted'" class="field">
            <label>白名单用户名（逗号分隔）</label>
            <input v-model="form.allowedUsers" class="input" placeholder="zhangsan, lisi" />
          </div>
          <div v-else-if="showInstallPolicy" class="field">
            <label>安装策略</label>
            <select v-model="form.install_policy" class="select">
              <option value="optional">可选（Default Off）</option>
              <option value="default_on">默认加入（可退）</option>
              <option value="required">强制（Required）</option>
            </select>
          </div>
        </div>

        <div class="field mt-12">
          <label>标签（逗号分隔）</label>
          <input v-model="form.tags" class="input" placeholder="如：工单, BMS" />
        </div>

        <div v-if="form.type === 'workflow'" class="field mt-12">
          <label>Workflow 画布（能力编排）</label>
          <button class="btn btn-primary" style="width: 100%" type="button" @click="openWorkflowEditor">
            直接打开空白画布（跳过本表单创建）
          </button>
          <div class="muted mt-8" style="font-size: 12px">
            或填写名称后点「创建并打开画布」，将自动进入该 Workflow 的编辑器。
          </div>
          <details class="mt-8">
            <summary class="muted" style="cursor: pointer; font-size: 12px">高级：粘贴 workflow.json</summary>
            <textarea
              v-model="form.workflowJson"
              class="textarea mt-8"
              rows="8"
              spellcheck="false"
            ></textarea><!-- bound to form.workflowJson -->
          </details>
        </div>

        <details class="mt-12">
          <summary class="muted" style="cursor: pointer; font-size: 12px">提交审核前请自检</summary>
          <ul class="checklist muted">
            <li v-for="(item, i) in REVIEW_CHECKLIST" :key="i">{{ item }}</li>
          </ul>
        </details>

        <div v-if="error" class="alert alert-error mt-12">{{ error }}</div>

        <div class="modal-foot">
          <span class="muted" style="font-size: 12px">{{ footHint }}</span>
          <div class="flex" style="gap: 10px">
            <button class="btn" type="button" @click="emit('close')">取消</button>
            <button class="btn btn-primary" type="button" :disabled="busy" @click="create">
              {{ busy ? '创建中…' : form.type === 'workflow' ? '创建并打开画布' : '创建草稿' }}
            </button>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.modal-mask {
  position: fixed;
  inset: 0;
  background: var(--overlay, rgba(15, 23, 42, 0.45));
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.modal {
  width: 720px;
  max-width: 100%;
  max-height: 90vh;
  overflow: auto;
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.55);
}
.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.modal-close {
  background: none;
  border: none;
  color: var(--muted);
  font-size: 16px;
  cursor: pointer;
}
.modal-close:hover { color: var(--text); }
.field { display: flex; flex-direction: column; gap: 6px; }
.field label { font-size: 13px; color: var(--muted); }
.mt-8 { margin-top: 8px; }
.mt-12 { margin-top: 12px; }
.mb-12 { margin-bottom: 12px; }
.modal-foot {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-top: 20px;
}
.intent-grid { display: grid; grid-template-columns: 1fr; gap: 10px; }
.intent-card {
  text-align: left;
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 14px 16px;
  cursor: pointer;
  color: var(--text);
}
.intent-card:hover {
  border-color: var(--primary);
  background: rgba(79, 140, 255, 0.1);
}
.intent-card span {
  display: block;
  margin-top: 6px;
  font-size: 13px;
  color: var(--muted);
  line-height: 1.45;
}
.shelf-grid { display: grid; grid-template-columns: 1fr; gap: 8px; }
.shelf-card {
  text-align: left;
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 10px 12px;
  cursor: pointer;
  color: var(--text);
}
.shelf-card span {
  display: block;
  margin-top: 4px;
  font-size: 12px;
  color: var(--muted);
  line-height: 1.4;
}
.shelf-card.active {
  border-color: var(--primary);
  background: rgba(79, 140, 255, 0.12);
}
.kind-row { display: flex; flex-wrap: wrap; gap: 8px; }
.kind-chip {
  background: var(--panel-2);
  border: 1px solid var(--border);
  color: var(--muted);
  border-radius: 8px;
  padding: 6px 12px;
  cursor: pointer;
  font-size: 13px;
}
.kind-chip em { font-style: normal; font-size: 11px; opacity: 0.8; }
.kind-chip.active { color: var(--primary); border-color: var(--primary); }
.kind-hint {
  margin-top: 8px;
  font-size: 12px;
  line-height: 1.5;
  padding: 8px 10px;
  background: var(--panel-2);
  border-radius: 8px;
}
.checklist {
  margin: 8px 0 0;
  padding-left: 18px;
  font-size: 12px;
  line-height: 1.55;
}
</style>
