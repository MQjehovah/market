<script setup>
import { STATUS_BADGE, STATUS_LABELS } from '../utils/format'

defineProps({ status: { type: String, required: true } })
</script>

<template>
  <span class="badge" :class="STATUS_BADGE[status] || 'badge'">{{ STATUS_LABELS[status] || status }}</span>
</template>
