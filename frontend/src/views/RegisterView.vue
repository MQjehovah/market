<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { setAuth } from '../stores/auth'

const router = useRouter()
const form = ref({ username: '', email: '', password: '', display_name: '', organization: '', team: '' })
const error = ref('')

async function submit() {
  error.value = ''
  try {
    const data = await api.post('/auth/register', form.value)
    setAuth(data.access_token, data.user)
    router.push('/')
  } catch (e) {
    error.value = e.message
  }
}
</script>

<template>
  <div class="auth-wrap">
    <div class="panel auth-card">
      <h2>注册账号</h2>
      <div v-if="error" class="alert alert-error">{{ error }}</div>
      <div class="field-row">
        <div class="field">
          <label>用户名</label>
          <input v-model="form.username" class="input" placeholder="3-64 位字母/数字/下划线" />
        </div>
        <div class="field">
          <label>显示名</label>
          <input v-model="form.display_name" class="input" placeholder="可选" />
        </div>
      </div>
      <div class="field">
        <label>邮箱</label>
        <input v-model="form.email" type="email" class="input" placeholder="you@company.com" />
      </div>
      <div class="field">
        <label>密码（至少 6 位）</label>
        <input v-model="form.password" type="password" class="input" />
      </div>
      <div class="field-row">
        <div class="field">
          <label>组织</label>
          <input v-model="form.organization" class="input" placeholder="如：数字中台部" />
        </div>
        <div class="field">
          <label>团队</label>
          <input v-model="form.team" class="input" placeholder="如：中台团队" />
        </div>
      </div>
      <button class="btn btn-primary" style="width: 100%" @click="submit">注 册</button>
      <div class="muted mt-16" style="text-align: center">
        已有账号？<router-link to="/login">去登录</router-link>
      </div>
    </div>
  </div>
</template>

<style scoped>
.auth-wrap { display: flex; justify-content: center; padding-top: 6vh; }
.auth-card { width: 460px; max-width: 100%; }
h2 { margin-top: 0; }
</style>
