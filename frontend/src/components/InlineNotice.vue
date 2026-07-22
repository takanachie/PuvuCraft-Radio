<script setup lang="ts">
withDefaults(
  defineProps<{
    tone?: 'info' | 'success' | 'warning' | 'danger'
    title?: string
  }>(),
  { tone: 'info', title: '' },
)
</script>

<template>
  <div class="inline-notice" :class="`inline-notice--${tone}`" role="status">
    <span class="inline-notice__mark" aria-hidden="true">{{ tone === 'success' ? '✓' : tone === 'danger' ? '!' : 'i' }}</span>
    <div>
      <strong v-if="title">{{ title }}</strong>
      <slot />
    </div>
  </div>
</template>
