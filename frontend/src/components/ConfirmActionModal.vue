<script setup>
/**
 * 通用确认弹层：替代 window.confirm，供管理台 / 我的能力复用。
 */
defineProps({
  show: { type: Boolean, default: false },
  title: { type: String, default: '确认' },
  body: { type: String, default: '' },
  okText: { type: String, default: '确定' },
  cancelText: { type: String, default: '取消' },
  danger: { type: Boolean, default: false },
  busy: { type: Boolean, default: false }
})
const emit = defineEmits(['ok', 'cancel'])
</script>

<template>
  <div v-if="show" class="modal-mask" @click.self="emit('cancel')">
    <div class="modal panel confirm-modal">
      <h3>{{ title }}</h3>
      <p class="muted">{{ body }}</p>
      <div class="modal-foot">
        <span />
        <div class="flex" style="gap: 10px">
          <button class="btn" type="button" :disabled="busy" @click="emit('cancel')">{{ cancelText }}</button>
          <button
            class="btn"
            :class="danger ? 'btn-danger' : 'btn-primary'"
            type="button"
            :disabled="busy"
            @click="emit('ok')"
          >{{ okText }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-mask {
  position: fixed; inset: 0; background: var(--overlay, rgba(15, 23, 42, 0.45)); z-index: 100;
  display: flex; align-items: center; justify-content: center; padding: 20px;
}
.confirm-modal { width: 420px; max-width: 100%; }
.confirm-modal h3 { margin: 0 0 8px; }
.modal-foot { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-top: 20px; }
</style>
