<script setup>
import DwsLoginPanel from '../components/DwsLoginPanel.vue'
</script>

<template>
  <div class="dws-page">
    <div class="dws-page-head">
      <h1>钉钉工作台 DWS</h1>
      <p>为当前账号授权钉钉个人身份，供零号员工网页调用 DWS 能力。</p>
    </div>
    <DwsLoginPanel auto-start />
  </div>
</template>

<style scoped>
.dws-page { max-width: 860px; margin: 0 auto; padding: 22px 18px 56px; }
.dws-page-head h1 { margin: 0 0 6px; font-size: 22px; }
.dws-page-head p { margin: 0 0 18px; color: var(--muted); font-size: 13px; }
</style>
