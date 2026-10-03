<template>
  <main class="chat-page">
    <header class="chat-head">
      <div>
        <h1>{{ capName || '会话调试' }}</h1>
        <p class="muted small">会话式能力（chatflow）：多轮对话，会话变量与历史由平台保存。</p>
      </div>
      <div class="flex" style="gap: 8px">
        <router-link v-if="capId" class="btn" :to="`/capabilities/${capId}`">能力详情</router-link>
        <button class="btn btn-primary" type="button" @click="newConversation">新会话</button>
      </div>
    </header>

    <div v-if="error" class="error mt-12">{{ error }}</div>

    <div class="chat-body">
      <aside class="conv-list">
        <div class="conv-list-head">会话</div>
        <button
          v-for="c in conversations"
          :key="c.id"
          class="conv-item"
          :class="{ active: c.id === conversationId }"
          type="button"
          @click="openConversation(c.id)"
        >
          <span class="conv-name">{{ c.title || '未命名会话' }}</span>
          <span class="muted conv-time">{{ formatDate(c.updated_at) }}</span>
        </button>
        <p v-if="!conversations.length" class="muted small" style="padding: 8px">暂无会话</p>
      </aside>

      <section class="chat-main">
        <div ref="scrollEl" class="messages">
          <div v-if="!messages.length && !sending" class="muted empty-hint">
            输入你的问题，开始一段对话。
          </div>
          <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
            <div class="bubble">{{ m.content }}</div>
          </div>
          <div v-if="sending" class="msg assistant">
            <div class="bubble muted">思考中…</div>
          </div>
        </div>
        <form class="composer" @submit.prevent="send">
          <textarea
            v-model="draft"
            class="textarea"
            rows="2"
            placeholder="输入消息，Enter 发送（Shift+Enter 换行）"
            @keydown.enter.exact.prevent="send"
          ></textarea>
          <button class="btn btn-primary" type="submit" :disabled="sending || !draft.trim()">
            {{ sending ? '发送中…' : '发送' }}
          </button>
        </form>
      </section>
    </div>
  </main>
</template>

<script setup>
import { nextTick, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { api, ApiError } from '../api'
import { formatDate } from '../utils/format'

const route = useRoute()
const capId = route.params.id

const capName = ref('')
const conversations = ref([])
const conversationId = ref('')
const messages = ref([])
const draft = ref('')
const sending = ref(false)
const error = ref('')
const scrollEl = ref(null)

function describeError(err) {
  if (err instanceof ApiError && err.status === 403) {
    return '无权运行该能力：需管理员，或该能力开放了运行权限。'
  }
  return err?.message || '请求失败'
}

async function scrollToBottom() {
  await nextTick()
  if (scrollEl.value) scrollEl.value.scrollTop = scrollEl.value.scrollHeight
}

async function loadDefinition() {
  try {
    const data = await api.get(`/workflows/${capId}/definition`)
    if (data.capability?.type !== 'workflow') {
      error.value = '该能力不是工作流'
      return
    }
    capName.value = data.capability.name
    await loadConversations()
  } catch (err) {
    error.value = describeError(err)
  }
}

async function loadConversations() {
  if (!capName.value) return
  try {
    conversations.value = await api.get(`/runtime/workflows/${capName.value}/conversations`)
  } catch (err) {
    error.value = describeError(err)
  }
}

async function openConversation(id) {
  conversationId.value = id
  error.value = ''
  try {
    messages.value = await api.get(`/runtime/workflows/conversations/${id}/messages`)
    await scrollToBottom()
  } catch (err) {
    error.value = describeError(err)
  }
}

function newConversation() {
  conversationId.value = ''
  messages.value = []
  draft.value = ''
  error.value = ''
}

async function send() {
  const query = draft.value.trim()
  if (!query || sending.value || !capName.value) return
  sending.value = true
  error.value = ''
  messages.value.push({ role: 'user', content: query })
  draft.value = ''
  await scrollToBottom()
  try {
    const res = await api.post(`/runtime/workflows/${capName.value}/chat`, {
      query,
      conversation_id: conversationId.value || undefined
    })
    conversationId.value = res.conversation_id
    messages.value.push({ role: 'assistant', content: res.answer || '(无回复)' })
    if (res.error) error.value = res.error
    await loadConversations()
  } catch (err) {
    error.value = describeError(err)
    messages.value.push({ role: 'assistant', content: '（请求失败）' })
  } finally {
    sending.value = false
    await scrollToBottom()
  }
}

onMounted(loadDefinition)
</script>

<style scoped>
.chat-page { max-width: 1080px; margin: 0 auto; padding: 24px 20px 40px; }
.chat-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
.chat-head h1 { margin: 0 0 4px; font-size: 22px; }
.small { font-size: 12px; }
.chat-body { display: grid; grid-template-columns: 240px 1fr; gap: 16px; margin-top: 18px; min-height: 60vh; }
.conv-list { border: 1px solid var(--border); border-radius: 12px; padding: 8px; background: var(--panel, #fff); height: fit-content; }
.conv-list-head { font-size: 12px; color: var(--muted); padding: 6px 8px; }
.conv-item { display: flex; flex-direction: column; align-items: flex-start; width: 100%; padding: 8px; border: 0; background: transparent; border-radius: 8px; cursor: pointer; text-align: left; }
.conv-item:hover { background: var(--hover, #f3f4f6); }
.conv-item.active { background: var(--primary-soft, #eef2ff); }
.conv-name { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%; }
.conv-time { font-size: 11px; }
.chat-main { display: flex; flex-direction: column; border: 1px solid var(--border); border-radius: 12px; overflow: hidden; background: var(--panel, #fff); }
.messages { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 10px; min-height: 360px; max-height: 60vh; }
.empty-hint { margin: auto; }
.msg { display: flex; }
.msg.user { justify-content: flex-end; }
.msg.assistant { justify-content: flex-start; }
.bubble { max-width: 76%; padding: 10px 14px; border-radius: 12px; white-space: pre-wrap; word-break: break-word; font-size: 14px; line-height: 1.6; }
.msg.user .bubble { background: var(--primary); color: #fff; border-bottom-right-radius: 4px; }
.msg.assistant .bubble { background: var(--hover, #f3f4f6); border-bottom-left-radius: 4px; }
.composer { display: flex; gap: 10px; padding: 12px; border-top: 1px solid var(--border); align-items: flex-end; }
.composer .textarea { flex: 1; min-height: 44px; }
@media (max-width: 720px) {
  .chat-body { grid-template-columns: 1fr; }
}
</style>
