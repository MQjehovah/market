<script setup>
import { ref } from 'vue'
import { api } from '../api'

const props = defineProps({
  kind: { type: String, required: true },
  name: { type: String, default: '' },
  hint: { type: String, default: '' }
})
const emit = defineEmits(['apply'])

const open = ref(false)
const description = ref('')
const instruction = ref('')
const busy = ref(false)
const error = ref('')
const notice = ref('')

const KIND_LABEL = { agent: '专家', skill: '技能', tool: '工具', mcp: '连接器' }
const label = KIND_LABEL[props.kind] || '能力'

async function run() {
  error.value = ''
  notice.value = ''
  const desc = description.value.trim()
  const ins = instruction.value.trim()
  if (!desc && !ins) {
    error.value = '请先填写需求描述'
    return
  }
  busy.value = true
  try {
    const body = await api.post('/authoring/generate', {
      kind: props.kind,
      name: props.name,
      description: desc,
      instruction: ins
    })
    emit('apply', body.fields || {}, body)
    notice.value = '已生成，请核对后保存（保存即新版本草稿）'
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <Teleport to="body">
    <div class="ai-dock">
      <transition name="ai-pop">
        <div v-if="open" class="ai-card">
          <div class="ai-head">
            <span class="ai-title">✨ AI 创作助手 · {{ label }}</span>
            <button type="button" class="ai-x" title="关闭" @click="open = false">×</button>
          </div>
          <div class="ai-body">
            <label class="ai-label">需求描述（要做一个什么样的{{ label }}）</label>
            <textarea
              v-model="description"
              class="textarea"
              rows="4"
              :placeholder="hint || '用一句话说清目标、使用场景、要点'"
            ></textarea>
            <label class="ai-label mt-8">补充要求（可选）</label>
            <input v-model="instruction" class="input" placeholder="如：输出用表格；只读不写；中文" />
            <div class="ai-actions">
              <button type="button" class="btn btn-primary" :disabled="busy" @click="run">
                {{ busy ? '生成中…（约十几秒）' : 'AI 生成草稿' }}
              </button>
            </div>
            <div class="ai-tip">生成内容填入编辑区，不自动保存；工具/连接器代码为骨架，需人工审查后再提交审核。</div>
            <div v-if="error" class="alert alert-error mt-8">{{ error }}</div>
            <div v-if="notice" class="alert alert-success mt-8">{{ notice }}</div>
          </div>
        </div>
      </transition>
      <button
        type="button"
        class="ai-fab"
        :class="{ active: open }"
        :title="open ? '收起 AI 创作助手' : 'AI 创作助手'"
        @click="open = !open"
      >
        <span v-if="!open">✨</span>
        <span v-else>×</span>
      </button>
    </div>
  </Teleport>
</template>

<style scoped>
.ai-dock {
  position: fixed;
  right: 24px;
  bottom: 24px;
  z-index: 2000;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 12px;
  pointer-events: none;
}
.ai-dock > * { pointer-events: auto; }
.ai-fab {
  width: 52px;
  height: 52px;
  border-radius: 50%;
  border: none;
  cursor: pointer;
  font-size: 22px;
  line-height: 1;
  color: #fff;
  background: linear-gradient(135deg, var(--primary, #4f7cff), var(--primary-strong, #2f5be0));
  box-shadow: 0 8px 22px rgba(47, 91, 224, 0.4);
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.ai-fab:hover { transform: translateY(-2px) scale(1.04); }
.ai-fab.active { background: var(--panel-2, #2a2f3a); color: var(--text, #eaeef7); }
.ai-card {
  width: 380px;
  max-width: calc(100vw - 40px);
  max-height: calc(100vh - 120px);
  overflow: auto;
  background: var(--panel, #171b23);
  border: 1px solid var(--border, #2a2f3a);
  border-radius: 14px;
  box-shadow: 0 16px 40px rgba(0, 0, 0, 0.35);
  padding: 14px 16px 16px;
}
.ai-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}
.ai-title { font-size: 14px; font-weight: 600; color: var(--text, #eaeef7); }
.ai-x {
  border: none;
  background: transparent;
  color: var(--muted, #8b93a7);
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
}
.ai-label { display: block; font-size: 12.5px; color: var(--muted, #8b93a7); margin-bottom: 6px; }
.ai-body .textarea,
.ai-body .input { width: 100%; }
.ai-actions { margin-top: 10px; }
.ai-tip { margin-top: 10px; font-size: 12px; color: var(--muted, #8b93a7); line-height: 1.5; }
.mt-8 { margin-top: 8px; }
.ai-pop-enter-active,
.ai-pop-leave-active { transition: opacity 0.15s ease, transform 0.15s ease; }
.ai-pop-enter-from,
.ai-pop-leave-to { opacity: 0; transform: translateY(8px); }
</style>
