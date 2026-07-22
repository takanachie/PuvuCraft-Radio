<script setup lang="ts">
import BrandMark from './BrandMark.vue'

defineProps<{
  eyebrow: string
  title: string
  lead: string
}>()
</script>

<template>
  <main id="main-content" class="public-shell">
    <section class="public-shell__signal" aria-hidden="true">
      <BrandMark />
      <div class="signal-illustration">
        <div class="signal-illustration__rings"></div>
        <div class="signal-illustration__tower"><span></span></div>
        <div class="signal-illustration__readout">
          <span>STEREO</span>
          <strong>88.8</strong>
          <small>MHz / ALWAYS ON</small>
        </div>
      </div>
      <p>独立频道持续发射。登录后，你听到的是此刻正在发生的声音。</p>
    </section>

    <section class="public-shell__panel">
      <div class="panel-screws" aria-hidden="true"><i></i><i></i><i></i><i></i></div>
      <header class="public-heading">
        <span class="eyebrow">{{ eyebrow }}</span>
        <h1>{{ title }}</h1>
        <p>{{ lead }}</p>
      </header>
      <slot />
    </section>
  </main>
</template>
