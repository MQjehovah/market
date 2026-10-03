<template>
  <main class="chat-page">
    <header class="chat-head">
      <div>
        <h1>会话应用</h1>
        <p class="muted small">多轮对话，会话变量与历史由平台保存。</p>
      </div>
      <router-link class="btn" :to="`/capabilities/${route.params.id}`">能力详情</router-link>
    </header>
    <WorkflowChat :id="route.params.id" />
  </main>
</template>

<script setup>
import { useRoute } from 'vue-router'

import WorkflowChat from '../components/WorkflowChat.vue'

const route = useRoute()
</script>

<style scoped>
.chat-page { max-width: 1080px; margin: 0 auto; padding: 24px 20px 40px; }
.chat-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
.chat-head h1 { margin: 0 0 4px; font-size: 22px; }
.small { font-size: 12px; }
</style>
