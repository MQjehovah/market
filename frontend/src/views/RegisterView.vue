<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { setAuth } from '../stores/auth'

const router = useRouter()
const form = ref({ username: '', email: '', password: '', display_name: '', organization: '' })
const error = ref('')
const loading = ref(false)

async function submit() {
  error.value = ''
  loading.value = true
  try {
    const data = await api.post('/auth/register', form.value)
    setAuth(data.access_token, data.user)
    router.push('/')
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

      <h1>注册</h1>
      <p class="hint">创建账号后即可发布与管理能力</p>

      <div v-if="error" class="alert alert-error">{{ error }}</div>

      <form @submit.prevent="submit">
        <div class="field-row">
          <div class="field">
            <label for="reg-username">用户名</label>
            <input
              id="reg-username"
              v-model="form.username"
              class="input"
              autocomplete="username"
              placeholder="字母/数字/下划线"
            />
          </div>
          <div class="field">
            <label for="reg-display">显示名</label>
            <input
              id="reg-display"
              v-model="form.display_name"
              class="input"
              autocomplete="nickname"
              placeholder="可选"
            />
          </div>
        </div>
        <div class="field">
          <label for="reg-email">邮箱</label>
          <input
            id="reg-email"
            v-model="form.email"
            type="email"
            class="input"
            autocomplete="email"
            placeholder="you@company.com"
          />
        </div>
        <div class="field">
          <label for="reg-password">密码（至少 6 位）</label>
          <input
            id="reg-password"
            v-model="form.password"
            type="password"
            class="input"
            autocomplete="new-password"
          />
        </div>
        <div class="field">
          <label for="reg-org">组织</label>
          <input
            id="reg-org"
            v-model="form.organization"
            class="input"
            autocomplete="organization"
            placeholder="如：数字中台部"
          />
        </div>
        <button class="btn btn-primary btn-block" type="submit" :disabled="loading">
          {{ loading ? '注册中…' : '注册' }}
        </button>
      </form>

      <p class="foot">
        已有账号？
        <router-link to="/login">去登录</router-link>
      </p>
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
  max-width: 420px;
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
</style>
