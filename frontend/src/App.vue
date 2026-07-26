<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import { session } from './session'

const route = useRoute()
const router = useRouter()
const online = ref(typeof navigator === 'undefined' ? true : navigator.onLine)

function handleOnline() {
  online.value = true
}

function handleOffline() {
  online.value = false
}

function handleUnauthorized() {
  session.clearUser()
  if (route.meta.requiresAuth) {
    void router.replace({ name: 'login', query: { reason: 'session', redirect: route.fullPath } })
  }
}

onMounted(() => {
  window.addEventListener('online', handleOnline)
  window.addEventListener('offline', handleOffline)
  window.addEventListener('radio:unauthorized', handleUnauthorized)
})

onBeforeUnmount(() => {
  window.removeEventListener('online', handleOnline)
  window.removeEventListener('offline', handleOffline)
  window.removeEventListener('radio:unauthorized', handleUnauthorized)
})
</script>

<template>
  <div class="app-shell">
    <div v-if="!online" class="network-banner" role="status">
      <span class="status-lamp status-lamp--danger" aria-hidden="true"></span>
      网络已断开，恢复连接后直播将自动回到实时位置
    </div>
    <div class="app-shell__route">
      <RouterView v-slot="{ Component }">
        <Transition name="route-fade" mode="out-in">
          <component :is="Component" />
        </Transition>
      </RouterView>
    </div>
    <footer class="site-footer" aria-label="网站备案信息">
      <a
        href="https://beian.miit.gov.cn/"
        target="_blank"
        rel="noopener noreferrer"
      >
        粤ICP备20002308号
      </a>
      <span class="site-footer__separator" aria-hidden="true">/</span>
      <span>本网站仅供个人使用，不对外提供真实服务。</span>
    </footer>
  </div>
</template>
