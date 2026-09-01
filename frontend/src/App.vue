<script setup>
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { api } from './api'
import { authState, setAuth } from './stores/auth'
import AppHeader from './components/AppHeader.vue'

const route = useRoute()

onMounted(() => {
  if (!authState.token) return
  api.get('/auth/me').then((user) => {
    if (user) setAuth(authState.token, user)
  }).catch(() => {})
})
const viewKey = computed(() => {
  if (route.path.startsWith('/admin')) return 'admin'
  return route.path + JSON.stringify(route.query)
})
const isBlank = computed(() => Boolean(route.meta.blank))
const mainClass = computed(() => ({
  'app-main-full': Boolean(route.meta.full) || isBlank.value,
  'app-main-admin': route.path.startsWith('/admin'),
  'app-main-blank': isBlank.value
}))
</script>

<template>
  <div class="app-shell" :class="{ 'app-shell-blank': isBlank }">
    <AppHeader v-if="!isBlank" />
    <main class="app-main" :class="mainClass">
      <router-view :key="viewKey" />
    </main>
  </div>
</template>
