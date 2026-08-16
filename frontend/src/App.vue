<script setup>
import AppHeader from './components/AppHeader.vue'
</script>

<template>
  <div class="app-shell">
    <AppHeader />
    <main class="app-main" :class="{ 'app-main-full': $route.meta.full }">
      <router-view />
    </main>
  </div>
</template>
