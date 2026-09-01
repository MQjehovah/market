<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import AppHeader from './components/AppHeader.vue'

const route = useRoute()
const viewKey = computed(() => {
  if (route.path.startsWith('/admin')) return 'admin'
  return route.path + JSON.stringify(route.query)
})
const mainClass = computed(() => ({
  'app-main-full': Boolean(route.meta.full),
  'app-main-admin': route.path.startsWith('/admin')
}))
</script>

<template>
  <div class="app-shell">
    <AppHeader />
    <main class="app-main" :class="mainClass">
      <router-view :key="viewKey" />
    </main>
  </div>
</template>
