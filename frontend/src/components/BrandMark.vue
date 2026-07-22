<script setup lang="ts">
defineProps<{
  compact?: boolean
}>()
</script>

<template>
  <div class="brand-mark" :class="{ 'brand-mark--compact': compact }" aria-label="PuvuCraft Radio">
    <span class="brand-mark__dial" aria-hidden="true">
      <i></i><i></i><i></i>
    </span>
    <span class="brand-mark__word">
      <strong>PuvuCraft</strong>
      <small>RADIO / LIVE TRANSMISSION</small>
    </span>
  </div>
</template>
