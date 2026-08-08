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
  <div class="auth-wrap">
    <div class="panel auth-card">
      <h2>登录市场</h2>
      <p class="muted">演示账号：admin / admin123（管理员），publisher / publisher123（发布者），user / user123456（普通用户）</p>
      <div v-if="error" class="alert alert-error">{{ error }}</div>
      <div class="field">
        <label>用户名</label>
        <input v-model="form.username" class="input" placeholder="请输入用户名" @keyup.enter="submit" />
      </div>
      <div class="field">
        <label>密码</label>
        <input v-model="form.password" type="password" class="input" placeholder="请输入密码" @keyup.enter="submit" />
      </div>
      <button class="btn btn-primary" style="width: 100%" :disabled="loading" @click="submit">
        {{ loading ? '登录中…' : '登 录' }}
      </button>
      <div class="muted mt-16" style="text-align: center">
        还没有账号？<router-link to="/register">立即注册</router-link>
      </div>
    </div>
  </div>
</template>

<style scoped>
.auth-wrap { display: flex; justify-content: center; padding-top: 8vh; }
.auth-card { width: 420px; max-width: 100%; }
h2 { margin-top: 0; }
</style>
