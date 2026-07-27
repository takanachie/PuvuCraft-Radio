<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  status?: string | null
  label?: string
}>()

const normalized = computed(() => (props.status || 'unknown').toLowerCase())
const tone = computed(() => {
  if (['live', 'approved', 'enabled', 'available', 'online', 'completed'].includes(normalized.value)) return 'good'
  if (['starting', 'pending', 'pending_approval', 'degraded', 'queued', 'ready', 'uploading', 'verifying', 'normalizing', 'placing'].includes(normalized.value)) return 'warn'
  if (['offline', 'rejected', 'disabled', 'error', 'unavailable', 'failed', 'cancelled', 'expired'].includes(normalized.value)) return 'bad'
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
    online: '正在收听',
    available: '可用',
    unavailable: '不可用',
    queued: '排队中',
    ready: '等待传输',
    uploading: '上传中',
    verifying: '校验中',
    normalizing: '规范化',
    placing: '迁移中',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
    expired: '已过期',
  }
  return labels[normalized.value] || props.status || '未知'
})
</script>

<template>
  <span class="status-badge" :class="`status-badge--${tone}`">
    <i aria-hidden="true"></i>{{ display }}
  </span>
</template>
