<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  status?: string | null
  label?: string
}>()

const normalized = computed(() => (props.status || 'unknown').toLowerCase())
const tone = computed(() => {
  if (['live', 'approved', 'enabled', 'available', 'online'].includes(normalized.value)) return 'good'
  if (['starting', 'pending', 'pending_approval', 'degraded'].includes(normalized.value)) return 'warn'
  if (['offline', 'rejected', 'disabled', 'error', 'unavailable'].includes(normalized.value)) return 'bad'
  return 'neutral'
})

const display = computed(() => {
  if (props.label) return props.label
  const labels: Record<string, string> = {
    live: '直播中',
    starting: '启动中',
    degraded: '信号异常',
    offline: '离线',
    stopped: '已停止',
    approved: '已批准',
    pending: '待审批',
    pending_approval: '待审批',
    rejected: '已拒绝',
    disabled: '已停用',
    available: '可用',
    unavailable: '不可用',
  }
  return labels[normalized.value] || props.status || '未知'
})
</script>

<template>
  <span class="status-badge" :class="`status-badge--${tone}`">
    <i aria-hidden="true"></i>{{ display }}
  </span>
</template>
