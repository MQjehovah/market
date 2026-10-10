<script setup>
import { computed, ref } from 'vue'

const props = defineProps({ html: { type: String, default: '' } })
const show = ref(false)

const doc = computed(
  () =>
    '<!doctype html><html><head><meta charset="utf-8"><style>body{margin:16px;font-family:\'Microsoft YaHei\',Arial,sans-serif;font-size:14px;color:#1f2329}</style></head><body>' +
    (props.html || '') +
    '</body></html>'
)
</script>

<template>
  <div class="hp">
    <div class="hp-bar">
      <button class="btn btn-sm" type="button" @click="show = !show">
        {{ show ? '查看源码' : '网页预览' }}
      </button>
    </div>
    <iframe v-if="show" class="hp-frame" sandbox="" :srcdoc="doc"></iframe>
  </div>
</template>

<style scoped>
.hp { margin-top: 6px; }
.hp-bar { margin-bottom: 6px; }
.hp-frame {
  display: block;
  width: 100%;
  height: 440px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #fff;
}
</style>
