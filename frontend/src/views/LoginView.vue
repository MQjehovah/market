<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { setAuth } from '../stores/auth'

const router = useRouter()
const route = useRoute()
const form = ref({ username: '', password: '' })
const error = ref('')
const loading = ref(false)

async function submit() {
  error.value = ''
  loading.value = true
  try {
    const data = await api.post('/auth/login', form.value)
    setAuth(data.access_token, data.user)
    router.push(route.query.redirect || '/')
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="auth-page">
    <div class="auth-box">
      <router-link to="/" class="brand">
        <span class="logo">AI</span>
        <span>能力目录</span>
      </router-link>

      <h1>登录</h1>
      <p class="hint">使用企业账号进入能力目录</p>

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

      <p class="foot">
        还没有账号？
        <router-link to="/register">立即注册</router-link>
      </p>

      <details class="demo">
        <summary>演示账号</summary>
        <p>admin / admin123 · publisher / publisher123 · user / user123456</p>
      </details>
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
  display: grid;
  place-items: center;
  font-size: 12px;
  font-weight: 700;
  color: #fff;
  background: var(--primary);
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

.foot {
  margin: 20px 0 0;
  text-align: center;
  color: var(--muted);
  font-size: 13px;
}

.demo {
  margin-top: 28px;
  padding-top: 16px;
  border-top: 1px solid var(--border);
  color: var(--muted);
  font-size: 12px;
}

.demo summary {
  cursor: pointer;
  user-select: none;
  list-style: none;
}

.demo summary::-webkit-details-marker {
  display: none;
}

.demo p {
  margin: 10px 0 0;
  line-height: 1.6;
}
</style>
