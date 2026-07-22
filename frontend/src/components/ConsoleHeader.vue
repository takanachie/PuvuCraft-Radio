<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { isApiError, userFacingError } from '../api/client'
import { session } from '../session'
import BrandMark from './BrandMark.vue'

defineProps<{
  section: string
}>()

const router = useRouter()
const loggingOut = ref(false)
const logoutError = ref('')

async function logout() {
  if (loggingOut.value) return
  loggingOut.value = true
  logoutError.value = ''
  try {
    await api.auth.logout()
    session.clearUser()
    await router.replace({ name: 'login' })
  } catch (cause) {
    if (isApiError(cause) && cause.status === 401) {
      session.clearUser()
      await router.replace({ name: 'login' })
    } else {
      logoutError.value = userFacingError(cause, '退出失败，请重试')
    }
  } finally {
    loggingOut.value = false
  }
}
</script>

<template>
  <header class="console-header">
    <RouterLink to="/radio" class="console-header__brand" aria-label="返回直播控制台">
      <BrandMark compact />
    </RouterLink>
    <div class="console-header__section">
      <span>CONTROL BUS</span>
      <strong>{{ section }}</strong>
    </div>
    <nav class="console-header__nav" aria-label="账号与控制台导航">
      <RouterLink v-if="session.state.user?.role === 'admin'" to="/admin">管理</RouterLink>
      <RouterLink to="/radio">收听</RouterLink>
      <span class="console-user" :title="session.state.user?.email">{{ session.state.user?.username }}</span>
      <span v-if="logoutError" class="console-header__error" role="alert" :title="logoutError">退出失败</span>
      <button class="button button--quiet button--small" type="button" :disabled="loggingOut" @click="logout">
        {{ loggingOut ? '退出中' : '退出' }}
      </button>
    </nav>
  </header>
</template>
