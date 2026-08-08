<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { authState, clearAuth } from '../stores/auth'

const router = useRouter()
const notifications = ref([])
const showNotify = ref(false)

const isLoggedIn = computed(() => Boolean(authState.token))
const isAdmin = computed(() => authState.user?.role === 'admin')
const canPublish = computed(() => ['admin', 'publisher'].includes(authState.user?.role))
const unreadCount = computed(() => notifications.value.filter((n) => !n.read).length)

async function loadNotifications() {
  if (!isLoggedIn.value) return
  try {
    notifications.value = await api.get('/notifications')
  } catch {
    notifications.value = []
  }
}

async function readAll() {
  await api.post('/notifications/read-all')
  await loadNotifications()
}

function logout() {
  clearAuth()
  router.push('/')
}

onMounted(loadNotifications)
</script>

<template>
  <header class="app-header">
    <div class="header-inner">
      <router-link to="/" class="brand">
        <span class="brand-logo">AI</span>
        <span class="brand-text">AI 能力公共市场</span>
      </router-link>

      <nav class="nav">
        <router-link to="/">能力浏览</router-link>
        <router-link v-if="canPublish" to="/publish">能力发布</router-link>
        <router-link v-if="isLoggedIn" to="/my">我的能力</router-link>
        <router-link v-if="isLoggedIn" to="/profile">个人中心</router-link>
        <router-link v-if="isAdmin" to="/admin">管理后台</router-link>
      </nav>

      <div class="header-right">
        <div v-if="isLoggedIn" class="notify-wrap">
          <button class="icon-btn" @click="showNotify = !showNotify">
            🔔<span v-if="unreadCount" class="notify-dot">{{ unreadCount }}</span>
          </button>
          <div v-if="showNotify" class="notify-panel panel">
            <div class="flex-between">
              <strong>通知</strong>
              <button class="btn btn-sm" @click="readAll">全部已读</button>
            </div>
            <div v-if="notifications.length === 0" class="muted mt-8">暂无通知</div>
            <div v-for="n in notifications" :key="n.id" class="notify-item" :class="{ unread: !n.read }">
              <div>{{ n.title }}</div>
              <div class="muted" style="font-size: 12px">{{ n.body }}</div>
            </div>
          </div>
        </div>

        <template v-if="isLoggedIn">
          <router-link to="/profile" class="user-chip">
            {{ authState.user.display_name || authState.user.username }}
            <span class="role-tag">{{ authState.user.role }}</span>
          </router-link>
          <button class="btn btn-sm" @click="logout">退出</button>
        </template>
        <template v-else>
          <router-link to="/login" class="btn btn-sm">登录</router-link>
          <router-link to="/register" class="btn btn-primary btn-sm">注册</router-link>
        </template>
      </div>
    </div>
  </header>
</template>

<style scoped>
.app-header {
  position: sticky;
  top: 0;
  z-index: 50;
  background: rgba(15, 20, 32, 0.92);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--border);
}
.header-inner {
  max-width: 1180px;
  margin: 0 auto;
  padding: 0 20px;
  height: 58px;
  display: flex;
  align-items: center;
  gap: 24px;
}
.brand { display: flex; align-items: center; gap: 10px; color: var(--text); }
.brand-logo {
  width: 32px; height: 32px; border-radius: 8px;
  background: linear-gradient(135deg, var(--primary), #7a5cff);
  display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: 13px; color: #fff;
}
.brand-text { font-size: 16px; font-weight: 600; }
.nav { display: flex; gap: 4px; flex: 1; }
.nav a { padding: 6px 12px; border-radius: 8px; color: var(--muted); font-size: 14px; }
.nav a:hover { color: var(--text); background: var(--panel-2); }
.nav a.router-link-exact-active { color: var(--primary); background: rgba(79,140,255,.1); }
.header-right { display: flex; align-items: center; gap: 12px; position: relative; }
.icon-btn {
  position: relative; background: none; border: none; color: var(--muted);
  font-size: 18px; cursor: pointer; padding: 4px;
}
.notify-dot {
  position: absolute; top: -2px; right: -4px; min-width: 16px; height: 16px;
  border-radius: 8px; background: var(--danger); color: #fff;
  font-size: 10px; display: flex; align-items: center; justify-content: center; padding: 0 3px;
}
.notify-panel {
  position: absolute; right: 0; top: 40px; width: 320px; max-height: 420px;
  overflow: auto; box-shadow: 0 12px 32px rgba(0,0,0,.4); z-index: 60;
}
.notify-item { padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
.notify-item.unread { color: var(--text); }
.notify-item:last-child { border-bottom: none; }
.user-chip { display: flex; align-items: center; gap: 6px; color: var(--text); }
.role-tag {
  font-size: 11px; padding: 1px 7px; border-radius: 999px;
  background: rgba(79,140,255,.15); color: #9cc2ff; border: 1px solid #2f6fe0;
}
</style>
