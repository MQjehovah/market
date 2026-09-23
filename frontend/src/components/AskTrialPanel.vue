<script setup>
/**
 * 员工侧「一句问话」试用：对助手发自然语言任务，展示回答与调用了哪些工具。
 * 连接器仍走 DebugCapabilityModal（选手动调工具）。
 */
import { computed, nextTick, ref, watch } from 'vue'
import { api } from '../api'
import { authState } from '../stores/auth'

const props = defineProps({
  /** 实际调用的助手名（skill 试用时应传 usedBy 助手名） */
  agentName: { type: String, required: true },
  agentVersion: { type: String, default: '' },
  /** 展示用：技能试用时写技能名 */
  subjectLabel: { type: String, default: '' },
  suggestions: { type: Array, default: () => [] },
  canRun: { type: Boolean, default: true },
  blockedHint: { type: String, default: '' }
})

const question = ref('')
const busy = ref(false)
const error = ref('')
const result = ref(null)
const inputEl = ref(null)

const chips = computed(() =>
  (props.suggestions || []).map(String).filter(Boolean).slice(0, 4)
)

const toolNames = computed(() => {
  const steps = result.value?.steps || []
  const names = []
  for (const s of steps) {
    if (s.kind && s.kind !== 'call') continue
    const n = s.name || s.tool || ''
    if (n && !names.includes(n)) names.push(n)
  }
  return names
})

watch(
  () => props.agentName,
  () => {
    question.value = ''
    error.value = ''
    result.value = null
  }
)

async function focus() {
  await nextTick()
  inputEl.value?.focus?.()
}

defineExpose({ focus })

function useChip(text) {
  question.value = text
  focus()
}

async function ask() {
  error.value = ''
  result.value = null
  const task = question.value.trim()
  if (!task) {
    error.value = '请先输入一句要问的话'
    return
  }
  if (!authState.token) {
    error.value = '请先登录'
    return
  }
  if (!props.canRun) {
    error.value = props.blockedHint || '当前不能试用'
    return
  }
  busy.value = true
  try {
    result.value = await api.post(`/runtime/agents/${encodeURIComponent(props.agentName)}/tasks`, {
      task
    })
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="ask-trial guide-block">
    <h3 class="guide-title">试用 · 问一句</h3>
    <p class="guide-lead muted">
      <template v-if="subjectLabel">通过助手「{{ agentName }}」验证「{{ subjectLabel }}」。</template>
      <template v-else>向「{{ agentName }}」发一句自然语言，看它怎么答{{ agentVersion ? `（v${agentVersion}）` : '' }}。</template>
      未配置 LLM 时为模拟回答。
    </p>
    <div v-if="chips.length" class="ask-chips">
      <button
        v-for="(c, i) in chips"
        :key="'chip-' + i"
        type="button"
        class="ask-chip"
        @click="useChip(c)"
      >{{ c }}</button>
    </div>
    <form class="ask-row" @submit.prevent="ask">
      <input
        ref="inputEl"
        v-model="question"
        class="input ask-input"
        type="text"
        maxlength="2000"
        placeholder="例如：帮我按这个场景处理一件事…"
        :disabled="busy"
        aria-label="试用问题"
      />
      <button class="btn btn-primary" type="submit" :disabled="busy || !canRun">
        {{ busy ? '提问中…' : '提问' }}
      </button>
    </form>
    <p v-if="!canRun && blockedHint" class="muted" style="font-size: 12px; margin: 8px 0 0">{{ blockedHint }}</p>
    <div v-if="error" class="alert alert-error mt-12">{{ error }}</div>
    <div v-if="result" class="ask-result mt-12">
      <div class="ask-meta muted">
        <span>{{ result.mode === 'simulated' ? '模拟' : '真实' }}执行</span>
        <span>·</span>
        <span>工具调用 {{ result.tool_calls || 0 }} 次</span>
        <span v-if="toolNames.length"> · {{ toolNames.join('、') }}</span>
      </div>
      <pre class="ask-output">{{ result.output }}</pre>
    </div>
  </div>
</template>

<style scoped>
.ask-chips { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 12px; }
.ask-chip {
  border: 1px solid var(--border);
  background: var(--panel-2);
  color: var(--text);
  border-radius: 999px;
  padding: 6px 12px;
  font-size: 12px;
  cursor: pointer;
  max-width: 100%;
  text-align: left;
}
.ask-chip:hover { border-color: var(--primary); color: var(--primary); }
.ask-row { display: flex; gap: 10px; align-items: center; }
.ask-input { flex: 1; min-width: 0; }
.ask-result {
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 12px 14px;
  background: var(--panel-2);
}
.ask-meta { font-size: 12px; margin-bottom: 8px; }
.ask-output {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13px;
  line-height: 1.55;
  font-family: inherit;
  color: var(--text);
}
.mt-12 { margin-top: 12px; }
</style>
