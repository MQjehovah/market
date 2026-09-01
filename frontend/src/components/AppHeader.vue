<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { authState, clearAuth } from '../stores/auth'
import { roleLabel } from '../utils/format'

const router = useRouter()
const route = useRoute()
const notifications = ref([])
const showNotify = ref(false)

const isLoggedIn = computed(() => Boolean(authState.token))
const isAdmin = computed(() => authState.user?.role === 'admin')
const unreadCount = computed(() => notifications.value.filter((n) => !n.read).length)
const roleText = computed(() => roleLabel(authState.user?.role))
const userLabel = computed(
  () => authState.user?.display_name || authState.user?.username || '未登录'
)

const isDiscoverActive = computed(
  () => route.path === '/' || route.path.startsWith('/capabilities/')
)
const isMyArea = computed(() => {
  if (route.path === '/my') return true
  return ['/agents/', '/skills/', '/tools/', '/mcp/', '/workflows/'].some((p) =>
    route.path.startsWith(p)
  )
})
const isPublishActive = computed(
  () => route.path === '/my' && String(route.query.publish || '') === '1'
)
const isMyAssetsActive = computed(() => isMyArea.value && !isPublishActive.value)
const isAdminActive = computed(() => route.path === '/admin')
const isProfileActive = computed(() => route.path === '/profile')

function goDiscover() {
  router.push({ path: '/' })
}

function goPublish() {
  router.push({ path: '/my', query: { publish: '1' } })
}

function goProfile() {
  router.push('/profile')
}

async function loadNotifications() {
  if (!isLoggedIn.value) {
    notifications.value = []
    return
  }
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
watch(() => authState.token, loadNotifications)
</script>

<template>
  <aside class="side-nav">
    <div class="side-top">
      <router-link to="/" class="brand" title="发现 · 安装 · 发布 · 审核 · 治理">
        <span class="brand-logo">AI</span>
        <span class="brand-text">
          <strong>能力目录</strong>
          <small>企业内部 SkillHub</small>
        </span>
      </router-link>
    </div>

    <div class="side-scroll">
      <div class="nav-group">
        <div class="nav-label">市场</div>
        <button
          type="button"
          class="nav-item"
          :class="{ active: isDiscoverActive }"
          @click="goDiscover"
        >
          发现
        </button>
      </div>

      <div class="nav-group">
        <div class="nav-label">工作台</div>
        <template v-if="isLoggedIn">
          <router-link
            to="/my"
            class="nav-item"
            active-class="nav-rr"
            exact-active-class="nav-rr"
            :class="{ active: isMyAssetsActive }"
          >
            我的能力
          </router-link>
          <button
            type="button"
            class="nav-item"
            :class="{ active: isPublishActive }"
            @click="goPublish"
          >
            发布能力
          </button>
        </template>
        <router-link v-else to="/login" class="nav-item">登录后管理</router-link>
      </div>

      <div v-if="isAdmin" class="nav-group">
        <div class="nav-label">治理</div>
        <router-link
          to="/admin"
          class="nav-item"
          active-class="nav-rr"
          exact-active-class="nav-rr"
          :class="{ active: isAdminActive }"
        >
          审核台
        </router-link>
      </div>
    </div>

    <div class="side-bottom">
      <div
        v-if="isLoggedIn"
        class="user-box"
        :class="{ active: isProfileActive }"
        role="button"
        tabindex="0"
        title="个人中心"
        @click="goProfile"
        @keyup.enter="goProfile"
      >
        <div class="user-avatar">{{ userLabel.slice(0, 1).toUpperCase() }}</div>
        <div class="user-meta">
          <div class="user-name">{{ userLabel }}</div>
          <div class="user-role">{{ roleText }}</div>
        </div>
        <div class="user-actions" @click.stop>
          <button type="button" class="icon-btn" title="通知" @click="showNotify = !showNotify">🔔</button>
          <button type="button" class="icon-btn" title="退出" @click="logout">⎋</button>
        </div>
        <span v-if="unreadCount" class="notify-dot">{{ unreadCount }}</span>
      </div>
      <div v-else class="guest-actions">
        <router-link to="/login" class="btn btn-primary btn-block btn-sm">登录</router-link>
        <router-link to="/register" class="btn btn-block btn-sm mt-8">注册</router-link>
      </div>

      <div v-if="showNotify && isLoggedIn" class="notify-panel panel" @click.stop>
        <div class="flex-between">
          <strong>通知</strong>
          <button class="btn btn-sm" type="button" @click="readAll">全部已读</button>
        </div>
        <div v-if="notifications.length === 0" class="muted mt-8">暂无通知</div>
        <div
          v-for="n in notifications"
          :key="n.id"
          class="notify-item"
          :class="{ unread: !n.read }"
        >
          <div>{{ n.title }}</div>
          <div class="muted" style="font-size: 12px">{{ n.body || n.content || '' }}</div>
        </div>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.side-nav {
  width: 232px;
  flex: none;
  height: 100vh;
  position: sticky;
  top: 0;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-right: 1px solid var(--border);
  z-index: 40;
}
.side-top { padding: 16px 14px 8px; }
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--text);
  padding: 6px 8px;
  border-radius: 12px;
}
.brand:hover { background: var(--panel-2); }
.brand-logo {
  width: 34px;
  height: 34px;
  border-radius: 10px;
  background: linear-gradient(145deg, #2f6bff, #1f56e0);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 13px;
  color: #fff;
  box-shadow: 0 4px 10px rgba(47, 107, 255, 0.25);
}
.brand-text { display: flex; flex-direction: column; line-height: 1.2; }
.brand-text strong { font-size: 14px; font-weight: 700; }
.brand-text small { color: var(--muted); font-size: 11px; margin-top: 2px; }

.side-scroll {
  flex: 1;
  overflow: auto;
  padding: 4px 10px 16px;
}
.nav-group { margin-top: 14px; }
.nav-label {
  padding: 0 10px 6px;
  font-size: 11px;
  color: #94a0b4;
  font-weight: 600;
  letter-spacing: 0.04em;
}
.nav-item {
  display: flex;
  align-items: center;
  width: 100%;
  border: none;
  background: transparent;
  text-align: left;
  padding: 9px 12px;
  margin-bottom: 2px;
  border-radius: 10px;
  color: #4b5872;
  font-size: 13px;
  cursor: pointer;
  text-decoration: none;
  position: relative;
}
.nav-item:hover { background: var(--panel-2); color: var(--text); }
.nav-item.active {
  background: var(--primary-soft);
  color: var(--primary);
  font-weight: 600;
}
.nav-item.active::before {
  content: '';
  position: absolute;
  left: 0;
  top: 8px;
  bottom: 8px;
  width: 3px;
  border-radius: 0 3px 3px 0;
  background: var(--primary);
}

.side-bottom {
  position: relative;
  padding: 12px;
  border-top: 1px solid var(--border);
}
.user-box {
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: 8px;
  align-items: center;
  padding: 8px;
  border-radius: 12px;
  background: var(--panel-2);
  position: relative;
  cursor: pointer;
}
.user-box:hover,
.user-box.active {
  outline: 1px solid #c9d8ff;
  background: #eef3ff;
}
.user-avatar {
  width: 32px;
  height: 32px;
  border-radius: 10px;
  background: #dfe7ff;
  color: var(--primary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
}
.user-name { font-size: 13px; font-weight: 600; line-height: 1.2; }
.user-role { font-size: 11px; color: var(--muted); }
.user-actions { display: flex; gap: 2px; }
.icon-btn {
  border: none;
  background: transparent;
  cursor: pointer;
  color: var(--muted);
  font-size: 14px;
  padding: 4px 6px;
  border-radius: 8px;
}
.icon-btn:hover { background: #fff; color: var(--text); }
.notify-dot {
  position: absolute;
  top: 4px;
  right: 4px;
  min-width: 16px;
  height: 16px;
  border-radius: 8px;
  background: var(--danger);
  color: #fff;
  font-size: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 3px;
}
.notify-panel {
  position: absolute;
  left: 12px;
  right: 12px;
  bottom: 72px;
  max-height: 320px;
  overflow: auto;
  z-index: 60;
  box-shadow: var(--shadow-lg);
}
.notify-item {
  padding: 8px 0;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
}
.notify-item.unread { color: var(--text); }
.notify-item:last-child { border-bottom: none; }
.btn-block { width: 100%; justify-content: center; }
.guest-actions { display: flex; flex-direction: column; }
.mt-8 { margin-top: 8px; }

@media (max-width: 900px) {
  .side-nav { width: 196px; }
  .brand-text small { display: none; }
}
</style>
