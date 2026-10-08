<script setup>
import { toasts, dismissToast } from '../utils/toast'
</script>

<template>
  <div class="toast-host" aria-live="polite" aria-relevant="additions">
    <button
      v-for="t in toasts"
      :key="t.id"
      type="button"
      class="toast"
      :class="'toast-' + t.type"
      @click="dismissToast(t.id)"
    >
      {{ t.message }}
    </button>
  </div>
</template>

<style scoped>
.toast-host {
  position: fixed;
  right: 20px;
  bottom: 20px;
  z-index: 240;
  display: flex;
  flex-direction: column-reverse;
  gap: 8px;
  max-width: min(360px, calc(100vw - 32px));
  pointer-events: none;
}
.toast {
  pointer-events: auto;
  text-align: left;
  width: 100%;
  padding: 10px 14px;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: var(--panel);
  color: var(--text);
  box-shadow: var(--shadow-lg);
  font-size: 13px;
  line-height: 1.5;
  cursor: pointer;
  animation: toast-in .18s ease;
}
.toast-success {
  border-color: #b6ebd2;
  background: var(--panel);
  color: #0e9f5c;
}
.toast-error {
  border-color: #f3b6b8;
  background: var(--panel);
  color: #c9373b;
}
@keyframes toast-in {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: none; }
}
</style>
