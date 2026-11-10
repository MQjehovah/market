<script setup>
/**
 * 员工侧「一句问话」试用：对专家发自然语言任务，SSE 流式展示思考/工具/正文。
 * 连接器仍走 DebugCapabilityModal（选手动调工具）。
 */
import { computed, nextTick, ref, watch } from 'vue'
import { api } from '../api'
import { authState } from '../stores/auth'

const props = defineProps({
  /** 实际调用的专家名（skill 试用时应传 usedBy 专家名） */
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

const liveReasoning = ref('')
const liveOutput = ref('')
const liveTools = ref([])
const liveRound = ref(0)
const reasoningOpen = ref(true)

const chips = computed(() =>
  (props.suggestions || []).map(String).filter(Boolean).slice(0, 4)
)

const payload = computed(() => result.value?.result || result.value || {})

const toolNames = computed(() => {
  const fromLive = liveTools.value.map((t) => t.name).filter(Boolean)
  if (fromLive.length) return [...new Set(fromLive)]
  const steps = payload.value?.steps || []
  const names = []
  for (const s of steps) {
    if (s.kind && s.kind !== 'call') continue
    const n = s.name || s.tool || ''
    if (n && !names.includes(n)) names.push(n)
  }
  return names
})

const displayOutput = computed(() => {
  if (payload.value?.output) return payload.value.output
  return liveOutput.value
})

const showLive = computed(() => busy.value || liveReasoning.value || liveTools.value.length || liveOutput.value)

watch(
  () => props.agentName,
  () => {
    question.value = ''
    error.value = ''
    result.value = null
    resetLive()
  }
)

function resetLive() {
  liveReasoning.value = ''
  liveOutput.value = ''
  liveTools.value = []
  liveRound.value = 0
  reasoningOpen.value = true
}

async function focus() {
  await nextTick()
  inputEl.value?.focus?.()
}

defineExpose({ focus })

function useChip(text) {
  question.value = text
  focus()
}

function onStreamEvent(ev) {
  if (!ev || !ev.type) return
  if (ev.type === 'start') {
    return
  }
  if (ev.type === 'round_start') {
    liveRound.value = Number(ev.round) || liveRound.value + 1
    return
  }
  if (ev.type === 'reasoning') {
    liveReasoning.value += ev.content || ''
    return
  }
  if (ev.type === 'token') {
    liveOutput.value += ev.content || ''
    return
  }
  if (ev.type === 'tool_start') {
    liveTools.value.push({
      name: ev.name || '',
      arguments: ev.arguments || '',
      result: '',
      pending: true
    })
    return
  }
  if (ev.type === 'tool_result') {
    const name = ev.name || ''
    for (let i = liveTools.value.length - 1; i >= 0; i -= 1) {
      const row = liveTools.value[i]
      if (row.name === name && row.pending) {
        row.result = ev.result || ''
        row.pending = false
        return
      }
    }
    liveTools.value.push({
      name,
      arguments: '',
      result: ev.result || '',
      pending: false
    })
    return
  }
  if (ev.type === 'done') {
    result.value = { result: ev.result || ev }
    if (ev.result?.output) liveOutput.value = ev.result.output
    return
  }
  if (ev.type === 'error') {
    error.value = ev.error || '执行失败'
  }
}

async function ask() {
  error.value = ''
  result.value = null
  resetLive()
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
    await api.streamPost(
      `/runtime/agents/${encodeURIComponent(props.agentName)}/tasks?stream=true&include_trace=true`,
      { task },
      onStreamEvent
    )
    if (!result.value && !error.value) {
      error.value = '流式结束但未收到结果'
    }
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
      <template v-if="subjectLabel">通过专家「{{ agentName }}」验证「{{ subjectLabel }}」。</template>
      <template v-else>向「{{ agentName }}」发一句自然语言，看它怎么答{{ agentVersion ? `（v${agentVersion}）` : '' }}。</template>
      提问记在你自己的模型额度上。
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
    <div v-if="showLive || result" class="ask-result mt-12">
      <div class="ask-meta muted">
        <span v-if="busy">执行中…</span>
        <span v-else>{{ payload.mode === 'simulated' ? '模拟执行' : '已回答' }}</span>
        <span>·</span>
        <span>工具调用 {{ payload.tool_calls ?? liveTools.length }} 次</span>
        <span v-if="toolNames.length"> · {{ toolNames.join('、') }}</span>
        <span v-if="liveRound"> · 第 {{ liveRound }} 轮</span>
      </div>

      <details v-if="liveReasoning" class="ask-reasoning" :open="reasoningOpen">
        <summary @click.prevent="reasoningOpen = !reasoningOpen">思考过程</summary>
        <pre class="ask-output ask-reasoning-body">{{ liveReasoning }}</pre>
      </details>

      <div v-if="liveTools.length" class="ask-tools">
        <div v-for="(t, i) in liveTools" :key="'tool-' + i" class="ask-tool">
          <div class="ask-tool-head">
            <code>{{ t.name }}</code>
            <span class="muted">{{ t.pending ? '执行中…' : '完成' }}</span>
          </div>
          <pre v-if="t.arguments && t.arguments !== '{}'" class="ask-tool-args">{{ t.arguments }}</pre>
          <pre v-if="t.result" class="ask-tool-result">{{ t.result }}</pre>
        </div>
      </div>

      <pre v-if="displayOutput" class="ask-output">{{ displayOutput }}</pre>
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
.ask-reasoning { margin: 0 0 10px; }
.ask-reasoning summary {
  cursor: pointer;
  font-size: 12px;
  color: var(--muted, #64748b);
  user-select: none;
}
.ask-reasoning-body {
  margin-top: 6px;
  opacity: 0.85;
  font-size: 12px;
  max-height: 180px;
  overflow: auto;
}
.ask-tools { display: flex; flex-direction: column; gap: 8px; margin: 0 0 10px; }
.ask-tool {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 10px;
  background: var(--panel, #fff);
}
.ask-tool-head {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
  margin-bottom: 4px;
}
.ask-tool-args,
.ask-tool-result {
  margin: 4px 0 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 11px;
  line-height: 1.45;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  max-height: 120px;
  overflow: auto;
  opacity: 0.9;
}
.mt-12 { margin-top: 12px; }
</style>
