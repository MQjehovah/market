<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../api'
import { authState } from '../stores/auth'
import { toast } from '../utils/toast'

const props = defineProps({
  capabilityId: { type: String, default: '' },
  autoStart: { type: Boolean, default: false },
  compact: { type: Boolean, default: false }
})
const emit = defineEmits(['authorized'])

const STATUS_LABEL = {
  unauthorized: '未授权',
  authorizing: '等待扫码',
  authorized: '已授权',
  expired: '已过期',
  error: '异常'
}
const account = ref(null)
const loading = ref(false)
const starting = ref(false)
const error = ref('')
const copied = ref('')
let timer = 0

const status = computed(() => account.value?.status || 'unauthorized')
const authorized = computed(() => status.value === 'authorized')
const loginCode = computed(() => account.value?.user_code || account.value?.login_code || '')
const loginUrl = computed(() => account.value?.verification_url || account.value?.login_url || '')
const profiles = computed(() => account.value?.profiles || [])

function fmt(value) {
  if (!value) return '—'
  const text = String(value)
  return text.replace('T', ' ').replace(/\.\d+.*$/, '')
}

async function load() {
  if (!authState.token) return
  loading.value = true
  try {
    account.value = await api.get('/dws/account')
    if (props.autoStart && !authorized.value && status.value !== 'authorizing') {
      await start()
    } else if (status.value === 'authorizing' && account.value?.login_session_id) {
      startPolling(account.value.login_session_id)
    }
  } catch (e) {
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function start() {
  if (!authState.token || starting.value) return
  starting.value = true
  error.value = ''
  stopPolling()
  try {
    account.value = await api.post('/dws/login/start', {})
    if (account.value?.session_id) startPolling(account.value.session_id)
  } catch (e) {
    error.value = e.message || '发起登录失败'
  } finally {
    starting.value = false
  }
}

function startPolling(sessionId) {
  stopPolling()
  const tick = async () => {
    try {
      const data = await api.get(`/dws/login/${encodeURIComponent(sessionId)}`)
      account.value = { ...account.value, ...data }
      if (data.status === 'authorized') {
        stopPolling()
        toast.success('钉钉 DWS 已授权')
        emit('authorized', data)
      } else if (data.status === 'error' || data.status === 'cancelled') {
        stopPolling()
      }
    } catch (e) {
      error.value = e.message || '轮询登录状态失败'
    }
  }
  timer = window.setInterval(tick, 3000)
  window.setTimeout(tick, 300)
}

function stopPolling() {
  if (timer) {
    window.clearInterval(timer)
    timer = 0
  }
}

async function copy(text, kind) {
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    copied.value = kind
    window.setTimeout(() => { copied.value = '' }, 1500)
  } catch {
    error.value = '复制失败，请手动选择复制'
  }
}

async function logout() {
  try {
    account.value = await api.post('/dws/logout', {})
    toast.success('已退出钉钉 DWS')
  } catch (e) {
    error.value = e.message || '退出失败'
  }
}

onMounted(load)
onBeforeUnmount(stopPolling)
</script>

<template>
  <section class="dws-panel" :class="{ compact }">
    <div class="dws-head">
      <div>
        <h3>钉钉 DWS 登录</h3>
        <p class="dws-sub">每个人使用自己的钉钉身份；凭据只保存在云端工作区，不进入数据库。</p>
      </div>
      <span class="dws-badge" :class="'is-' + status">{{ STATUS_LABEL[status] || status }}</span>
    </div>

    <div v-if="!authState.token" class="dws-empty">
      <router-link class="btn btn-primary btn-sm" :to="{ path: '/login', query: { redirect: $route.fullPath } }">
        登录后配置
      </router-link>
    </div>

    <div v-else-if="loading && !account" class="dws-empty">加载中…</div>

    <template v-else>
      <div v-if="authorized" class="dws-profile">
        <div class="dws-dot" />
        <div class="dws-profile-main">
          <strong>{{ account.dingtalk_user_name || account.corp_name || '已授权' }}</strong>
          <span>{{ account.corp_name || '钉钉' }}<template v-if="account.current_profile"> · {{ account.current_profile }}</template></span>
        </div>
        <div class="dws-expiry">
          <span>Access 到期 {{ fmt(account.access_expires_at) }}</span>
          <span>Refresh 到期 {{ fmt(account.refresh_expires_at) }}</span>
        </div>
      </div>

      <div v-if="status === 'authorizing'" class="dws-login">
        <p class="dws-login-hint">用钉钉扫码或打开链接授权：</p>
        <div v-if="loginCode" class="dws-code-row">
          <code class="dws-code">{{ loginCode }}</code>
          <button class="btn btn-sm" type="button" @click="copy(loginCode, 'code')">
            {{ copied === 'code' ? '已复制' : '复制授权码' }}
          </button>
        </div>
        <div v-if="loginUrl" class="dws-link-row">
          <a :href="loginUrl" target="_blank" rel="noopener" class="dws-link">{{ loginUrl }}</a>
          <button class="btn btn-sm" type="button" @click="copy(loginUrl, 'url')">
            {{ copied === 'url' ? '已复制' : '复制链接' }}
          </button>
        </div>
        <p class="dws-muted">授权码 15 分钟内有效；页面每 3 秒自动检查状态。</p>
      </div>

      <div v-if="profiles.length > 1" class="dws-profiles">
        <p class="dws-muted">当前账号有多个钉钉 profile：</p>
        <div v-for="p in profiles" :key="p.profile" class="dws-profile-row" :class="{ current: p.isCurrent }">
          <span>{{ p.corpName || p.corpId }} · {{ p.userName || p.userId }}</span>
          <span class="dws-muted mono">{{ p.profile }}</span>
        </div>
      </div>

      <p v-if="account?.last_error" class="dws-error">{{ account.last_error }}</p>
      <p v-if="error" class="dws-error">{{ error }}</p>

      <div class="dws-actions">
        <button
          v-if="!authorized || status === 'expired' || status === 'error'"
          class="btn btn-primary btn-sm"
          type="button"
          :disabled="starting"
          @click="start"
        >{{ starting ? '正在发起…' : status === 'authorizing' ? '重新发起' : '扫码授权' }}</button>
        <button v-if="authorized" class="btn btn-sm" type="button" @click="start">重新授权</button>
        <button v-if="authorized" class="btn btn-sm install-ghost" type="button" @click="logout">退出登录</button>
      </div>

      <p class="dws-note">
        电脑端和网页端同时使用同一个钉钉账号时，refresh token 可能互相踢下线；被踢后重新扫码即可。
      </p>
    </template>
  </section>
</template>

<style scoped>
.dws-panel {
  border: 1px solid var(--border, #e5e7eb);
  border-radius: 12px;
  background: var(--bg-card, #fff);
  padding: 16px 18px;
  margin-bottom: 18px;
}
.dws-panel.compact { padding: 12px 14px; }
.dws-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.dws-head h3 { margin: 0 0 4px; font-size: 15px; }
.dws-sub { margin: 0; color: var(--text-3, #6b7280); font-size: 12px; line-height: 1.6; }
.dws-badge { flex: none; border-radius: 999px; padding: 2px 9px; font-size: 11px; border: 1px solid currentColor; }
.dws-badge.is-authorized { color: #15803d; background: #f0fdf4; }
.dws-badge.is-authorizing { color: #b45309; background: #fffbeb; }
.dws-badge.is-error, .dws-badge.is-expired { color: #b91c1c; background: #fef2f2; }
.dws-badge.is-unauthorized { color: #6b7280; background: #f9fafb; }
.dws-empty { padding: 16px 0 4px; color: var(--text-3, #6b7280); font-size: 13px; }
.dws-profile { display: flex; align-items: center; gap: 10px; margin-top: 14px; padding: 10px 12px; border-radius: 9px; background: #f0fdf4; }
.dws-dot { width: 8px; height: 8px; border-radius: 50%; background: #22c55e; flex: none; }
.dws-profile-main { display: flex; flex-direction: column; min-width: 0; }
.dws-profile-main strong { font-size: 13px; }
.dws-profile-main span { font-size: 11px; color: #4b5563; }
.dws-expiry { margin-left: auto; display: flex; flex-direction: column; gap: 2px; font-size: 11px; color: #4b5563; text-align: right; }
.dws-login { margin-top: 14px; }
.dws-login-hint { margin: 0 0 8px; font-size: 13px; }
.dws-code-row, .dws-link-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.dws-code { font-size: 22px; letter-spacing: 2px; font-weight: 700; padding: 4px 10px; border-radius: 8px; background: #f3f4f6; }
.dws-link { font-size: 12px; color: #2563eb; word-break: break-all; flex: 1; min-width: 200px; }
.dws-muted { color: var(--text-3, #6b7280); font-size: 12px; margin: 0; }
.dws-profiles { margin-top: 14px; }
.dws-profile-row { display: flex; justify-content: space-between; gap: 10px; padding: 6px 8px; border-radius: 6px; font-size: 12px; }
.dws-profile-row.current { background: #eff6ff; }
.mono { font-family: Consolas, monospace; }
.dws-error { margin: 10px 0 0; color: #b91c1c; font-size: 12px; white-space: pre-wrap; }
.dws-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 14px; }
.dws-note { margin: 12px 0 0; color: var(--text-3, #9ca3af); font-size: 11px; line-height: 1.5; }
</style>
