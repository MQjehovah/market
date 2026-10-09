<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { authState, clearAuth } from '../stores/auth'
import { notifyState, loadNotifications, clearNotifications, markNoticeRead, markAllRead } from '../stores/notifications'
import { adminState } from '../stores/admin'
import { roleLabel } from '../utils/format'
import logoUrl from '../assets/logo.svg'

const router = useRouter()
const route = useRoute()
const showNotify = ref(false)
const navOpen = ref(false)

const isLoggedIn = computed(() => Boolean(authState.token))
const isAdmin = computed(() => authState.user?.role === 'admin')
const unreadCount = computed(() => notifyState.items.filter((n) => !n.read).length)
const roleText = computed(() => roleLabel(authState.user?.role))
const userLabel = computed(
  () => authState.user?.name || authState.user?.username || '未登录'
)

const isDiscoverActive = computed(
  () => route.path === '/' || route.path.startsWith('/capabilities/')
)
const isMyActive = computed(() =>
  ['/agents/', '/skills/', '/tools/', '/mcp/', '/workflows/', '/rules/', '/commands/', '/hooks/'].some((p) =>
    route.path.startsWith(p)
  )
)
const isMyCapsActive = computed(() => route.path === '/my')
const adminSection = computed(() =>
  route.path.startsWith('/admin/') ? String(route.params.section || '') : ''
)
const isProfileActive = computed(() => route.path === '/profile')
const reviewingCount = computed(() => adminState.reviewingCount)

function goDiscover() {
  router.push({ path: '/' })
}

function goProfile() {
  router.push('/profile')
}

async function refreshNotifications() {
  if (!isLoggedIn.value) {
    clearNotifications()
    return
  }
  await loadNotifications()
}

async function loadAdminBadge() {
  if (!isAdmin.value) {
    adminState.reviewingCount = 0
    return
  }
  try {
    const s = await api.get('/admin/stats')
    adminState.reviewingCount = s.reviewing_count || 0
  } catch {
    adminState.reviewingCount = 0
  }
}

async function openNotice(n) {
  showNotify.value = false
  if (!n.read) await markNoticeRead(n)
  if (n.link && n.link.startsWith('/') && !n.link.startsWith('//')) {
    router.push(n.link)
  }
}

async function readAll() {
  try {
    await markAllRead()
  } catch {
    /* 保持原列表 */
  }
}

function logout() {
  clearAuth()
  clearNotifications()
  router.push('/')
}

onMounted(() => {
  refreshNotifications()
  loadAdminBadge()
})
watch(() => authState.token, () => {
  refreshNotifications()
  loadAdminBadge()
})
watch(isAdmin, loadAdminBadge)
watch(() => route.fullPath, () => {
  navOpen.value = false
})
watch(navOpen, (open) => {
  document.body.style.overflow = open ? 'hidden' : ''
})
onUnmounted(() => {
  document.body.style.overflow = ''
})
</script>

<template>
  <div class="app-nav">
    <header class="mobile-bar">
      <button
        type="button"
        class="nav-toggle"
        :aria-expanded="navOpen ? 'true' : 'false'"
        aria-controls="side-nav"
        @click="navOpen = !navOpen"
      >
        {{ navOpen ? '关闭' : '菜单' }}
      </button>
      <router-link to="/" class="mobile-brand">企业AI能力平台</router-link>
    </header>
    <div v-if="navOpen" class="nav-scrim" @click="navOpen = false"></div>
  <aside id="side-nav" class="side-nav" :class="{ open: navOpen }">
    <div class="side-top">
      <button type="button" class="nav-close" aria-label="关闭菜单" @click="navOpen = false">关闭</button>
      <router-link to="/" class="brand" title="发现 · 安装 · 发布 · 治理">
        <img class="brand-logo" :src="logoUrl" alt="Rosiwit" />
        <span class="brand-text">
          <strong>企业AI能力平台</strong>
          <small>统一纳管 · 分发 · 装配</small>
        </span>
      </router-link>
    </div>

    <div class="side-scroll">
      <div class="nav-group">
        <div class="nav-label">能力平台</div>
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
            :class="{ active: isMyCapsActive || isMyActive }"
          >
            我的能力
          </router-link>
          <router-link
            to="/approvals"
            class="nav-item"
            :class="{ active: route.path === '/approvals' }"
          >
            审批中心
          </router-link>
        </template>
        <p v-else class="nav-hint">登录后可加入和发布</p>
      </div>

      <div v-if="isAdmin" class="nav-group">
        <div class="nav-label">治理</div>
        <router-link
          to="/admin/caps"
          class="nav-item"
          :class="{ active: adminSection === 'caps' }"
        >
          能力管理
          <span v-if="reviewingCount" class="nav-count">{{ reviewingCount }}</span>
        </router-link>
        <router-link
          to="/admin/users"
          class="nav-item"
          :class="{ active: adminSection === 'users' }"
        >
          用户管理
        </router-link>
        <router-link
          to="/admin/gateway"
          class="nav-item"
          :class="{ active: adminSection === 'gateway' }"
        >
          MCP 网关
        </router-link>
        <router-link
          to="/admin/tokens"
          class="nav-item"
          :class="{ active: adminSection === 'tokens' }"
        >
          服务令牌
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
          <button type="button" class="icon-btn" title="通知" aria-label="通知" @click="showNotify = !showNotify">
            <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
              <path
                fill="none"
                stroke="currentColor"
                stroke-width="1.75"
                stroke-linecap="round"
                stroke-linejoin="round"
                d="M6.5 9.5a5.5 5.5 0 0 1 11 0c0 4 1.5 5.5 1.5 5.5H5s1.5-1.5 1.5-5.5"
              />
              <path
                fill="none"
                stroke="currentColor"
                stroke-width="1.75"
                stroke-linecap="round"
                d="M10 18.5a2 2 0 0 0 4 0"
              />
            </svg>
            <span v-if="unreadCount" class="notify-dot">{{ unreadCount > 9 ? '9+' : unreadCount }}</span>
          </button>
          <button type="button" class="icon-btn" title="退出登录" aria-label="退出登录" @click="logout">
            <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
              <path
                fill="none"
                stroke="currentColor"
                stroke-width="1.75"
                stroke-linecap="round"
                stroke-linejoin="round"
                d="M10 4H6.5A2.5 2.5 0 0 0 4 6.5v11A2.5 2.5 0 0 0 6.5 20H10"
              />
              <path
                fill="none"
                stroke="currentColor"
                stroke-width="1.75"
                stroke-linecap="round"
                stroke-linejoin="round"
                d="M14 8l4 4-4 4M18 12H9"
              />
            </svg>
          </button>
        </div>
      </div>
      <div v-else class="guest-actions">
        <router-link to="/login" class="btn btn-primary btn-block btn-sm">登录</router-link>
      </div>

      <div v-if="showNotify && isLoggedIn" class="notify-panel panel" @click.stop>
        <div class="flex-between">
          <strong>通知</strong>
          <button class="btn btn-sm" type="button" @click="readAll">全部已读</button>
        </div>
        <div v-if="notifyState.error" class="muted mt-8">{{ notifyState.error }}</div>
        <div v-else-if="notifyState.items.length === 0" class="muted mt-8">暂无通知</div>
        <button
          v-for="n in notifyState.items"
          :key="n.id"
          type="button"
          class="notify-item"
          :class="{ unread: !n.read, link: !!n.link }"
          @click="openNotice(n)"
        >
          <div>{{ n.title }}</div>
          <div class="muted" style="font-size: 12px">{{ n.body || n.content || '' }}</div>
        </button>
      </div>
    </div>
  </aside>
  </div>
</template>

<style scoped>
.app-nav {
  width: 232px;
  flex: none;
  height: 100vh;
  position: sticky;
  top: 0;
}
.mobile-bar,
.nav-scrim,
.nav-close { display: none; }
.side-nav {
  width: 100%;
  height: 100%;
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
  display: block;
  object-fit: contain;
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
.nav-hint {
  margin: 0;
  padding: 8px 12px;
  font-size: 12px;
  line-height: 1.45;
  color: var(--muted);
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
.nav-count {
  margin-left: auto;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 9px;
  background: var(--danger);
  color: #fff;
  font-size: 11px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
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
.user-actions { display: flex; gap: 2px; flex-shrink: 0; }
.icon-btn {
  position: relative;
  border: none;
  background: transparent;
  cursor: pointer;
  color: var(--muted);
  padding: 6px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  line-height: 0;
}
.icon-btn svg { display: block; }
.icon-btn:hover { background: #fff; color: var(--primary); }
.notify-dot {
  position: absolute;
  top: 0;
  right: 0;
  min-width: 14px;
  height: 14px;
  border-radius: 7px;
  background: var(--danger);
  color: #fff;
  font-size: 9px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 3px;
  line-height: 1;
  pointer-events: none;
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
  display: block;
  width: 100%;
  text-align: left;
  padding: 8px 0;
  border: none;
  border-bottom: 1px solid var(--border);
  background: transparent;
  font-size: 13px;
  color: inherit;
}
.notify-item.link { cursor: pointer; }
.notify-item.link:hover { color: var(--primary); }
.notify-item.unread { font-weight: 650; border-left: 3px solid var(--primary); padding-left: 8px; }
.notify-item:last-child { border-bottom: none; }
.btn-block { width: 100%; justify-content: center; }
.guest-actions { display: flex; flex-direction: column; }
.mt-8 { margin-top: 8px; }

@media (max-width: 900px) {
  .app-nav {
    width: 100%;
    height: auto;
    position: static;
  }
  .mobile-bar {
    display: flex;
    align-items: center;
    gap: 12px;
    height: 52px;
    padding: 0 12px;
    background: var(--panel);
    border-bottom: 1px solid var(--border);
    position: sticky;
    top: 0;
    z-index: 45;
  }
  .nav-toggle,
  .nav-close {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border: 1px solid var(--border-strong);
    background: var(--panel);
    color: var(--text);
    border-radius: 8px;
    padding: 6px 10px;
    font-size: 13px;
    cursor: pointer;
  }
  .mobile-brand {
    color: var(--text);
    font-weight: 700;
    font-size: 14px;
  }
  .nav-scrim {
    display: block;
    position: fixed;
    inset: 0;
    background: var(--overlay);
    z-index: 60;
  }
  .side-nav {
    position: fixed;
    top: 0;
    left: 0;
    width: min(280px, 86vw);
    height: 100vh;
    transform: translateX(-105%);
    transition: transform .2s ease;
    z-index: 70;
    box-shadow: var(--shadow-lg);
  }
  .side-nav.open { transform: none; }
  .nav-close { margin: 0 0 8px auto; }
  .side-top { display: flex; flex-direction: column; }
}
</style>
