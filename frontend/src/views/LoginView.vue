<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { clearAuth, setAuth } from '../stores/auth'
import logoUrl from '../assets/logo.svg'

const router = useRouter()
const route = useRoute()
const form = ref({ username: '', password: '' })
const error = ref('')
const loading = ref(false)
const ssoLoading = ref(false)

function safeRedirect(raw) {
  if (typeof raw !== 'string') return '/'
  const value = raw.trim()
  if (!value.startsWith('/') || value.startsWith('//') || value.startsWith('/\\')) return '/'
  if (value.includes('\\') || value.includes('://')) return '/'
  const path = value.split('?')[0].split('#')[0]
  if (path === '/login' || path.startsWith('/login/')) return '/'
  return value
}

async function submit() {
  error.value = ''
  loading.value = true
  try {
    const data = await api.post('/auth/login', form.value)
    setAuth(data.access_token, data.user)
    router.push(safeRedirect(route.query.redirect))
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function loginWithSso() {
  if (ssoLoading.value) return
  ssoLoading.value = true
  error.value = ''
  const base = (import.meta.env.BASE_URL || '/').replace(/\/$/, '') + '/api/auth/sso/start'
  const next = safeRedirect(route.query.redirect)
  window.location.href = next === '/' ? base : `${base}?next=${encodeURIComponent(next)}`
}

async function handleSsoCallback() {
  const queryError = route.query.error
  if (typeof queryError === 'string' && queryError) error.value = queryError
  const token = route.query.sso_token
  if (typeof token !== 'string' || !token) return
  ssoLoading.value = true
  error.value = ''
  try {
    setAuth(token, { username: '', role: 'user' })
    const user = await api.get('/auth/me')
    setAuth(token, user)
    await router.replace(safeRedirect(route.query.redirect))
  } catch (e) {
    clearAuth()
    error.value = e.message || 'SSO 登录失败'
    const back = safeRedirect(route.query.redirect)
    await router.replace(back === '/' ? { path: '/login' } : { path: '/login', query: { redirect: back } })
  } finally {
    ssoLoading.value = false
  }
}

onMounted(handleSsoCallback)
</script>

<template>
  <div class="auth-page">
    <div class="auth-box">
      <router-link to="/" class="brand">
        <img class="logo" :src="logoUrl" alt="Rosiwit" />
        <span>企业AI能力平台</span>
      </router-link>

      <h1>登录</h1>
      <p class="hint">使用企业账号登录平台</p>

      <div v-if="error" class="alert alert-error">{{ error }}</div>

      <form @submit.prevent="submit">
        <div class="field">
          <label for="login-username">用户名</label>
          <input
            id="login-username"
            v-model="form.username"
            class="input"
            autocomplete="username"
            placeholder="请输入用户名"
          />
        </div>
        <div class="field">
          <label for="login-password">密码</label>
          <input
            id="login-password"
            v-model="form.password"
            type="password"
            class="input"
            autocomplete="current-password"
            placeholder="请输入密码"
          />
        </div>
        <button class="btn btn-primary btn-block" type="submit" :disabled="loading">
          {{ loading ? '登录中…' : '登录' }}
        </button>
      </form>

      <p class="sso-or">或</p>
      <button class="btn btn-block" type="button" :disabled="ssoLoading" @click="loginWithSso">
        {{ ssoLoading ? '正在跳转…' : '企业 SSO 登录' }}
      </button>
      <p class="sso-hint">将打开公司统一登录页，可用钉钉扫码或工号密码。</p>

    </div>
  </div>
</template>

<style scoped>
.auth-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 32px 20px;
  background: var(--bg);
}

.auth-box {
  width: 100%;
  max-width: 380px;
}

.brand {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  color: var(--text);
  font-weight: 600;
  margin-bottom: 36px;
}

.brand:hover {
  color: var(--text);
}

.logo {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: block;
  object-fit: contain;
}

h1 {
  margin: 0 0 6px;
  font-size: 24px;
  font-weight: 650;
  letter-spacing: -0.02em;
}

.hint {
  margin: 0 0 28px;
  color: var(--muted);
  font-size: 14px;
}

.field {
  margin-bottom: 14px;
}

.btn-block {
  margin-top: 8px;
  min-height: 40px;
}

.sso-or {
  margin: 16px 0 8px;
  text-align: center;
  color: var(--muted);
  font-size: 12px;
}

.sso-hint {
  margin: 8px 0 0;
  text-align: center;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.5;
}

.foot {
  margin: 20px 0 0;
  text-align: center;
  color: var(--muted);
  font-size: 13px;
}
</style>
