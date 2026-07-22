<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { isApiError, userFacingError } from '../api/client'
import PublicFrame from '../components/PublicFrame.vue'
import InlineNotice from '../components/InlineNotice.vue'
import { session } from '../session'

const route = useRoute()
const router = useRouter()
const form = reactive({ username: '', password: '' })
const submitting = ref(false)
const error = ref('')

const pageNotice = computed(() => {
  if (route.query.reason === 'session') return '会话已失效，请重新登录后继续收听。'
  if (route.query.initialized === '1') return '电台初始化完成。请使用管理员账号登录。'
  return session.state.setupError ? '暂时无法确认电台初始化状态，仍可尝试登录。' : ''
})

function loginError(cause: unknown): string {
  if (isApiError(cause)) {
    if (['account_not_approved', 'account_pending', 'pending_approval', 'user_pending'].includes(cause.code)) {
      return '账号仍在等待管理员审批。审批通过后即可登录。'
    }
    if (['account_disabled', 'user_disabled', 'account_rejected'].includes(cause.code)) {
      return '账号当前无法登录，请联系电台管理员。'
    }
    if (cause.status === 401) return '用户名或密码不正确。'
  }
  return userFacingError(cause, '登录失败，请稍后重试')
}

async function submit() {
  if (submitting.value) return
  error.value = ''
  submitting.value = true
  try {
    const user = await api.auth.login({ username: form.username.trim(), password: form.password })
    session.setUser(user)
    const redirect = typeof route.query.redirect === 'string'
      && route.query.redirect.startsWith('/')
      && !route.query.redirect.startsWith('//')
      ? route.query.redirect
      : '/radio'
    await router.replace(redirect)
  } catch (cause) {
    error.value = loginError(cause)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <PublicFrame eyebrow="Operator access / 01" title="接入直播信号" lead="使用已获批准的账号进入同步直播控制台。">
    <InlineNotice v-if="pageNotice" :tone="route.query.initialized === '1' ? 'success' : 'warning'">
      {{ pageNotice }}
    </InlineNotice>
    <form class="console-form" novalidate @submit.prevent="submit">
      <div class="field">
        <label for="login-username">用户名</label>
        <input
          id="login-username"
          v-model="form.username"
          name="username"
          autocomplete="username"
          required
          autofocus
        />
      </div>
      <div class="field">
        <label for="login-password">密码</label>
        <input
          id="login-password"
          v-model="form.password"
          name="password"
          type="password"
          autocomplete="current-password"
          required
        />
      </div>
      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <button class="button button--primary button--wide" type="submit" :disabled="submitting || !form.username || !form.password">
        <span class="button__lamp" aria-hidden="true"></span>
        {{ submitting ? '正在校验信号…' : '登录并开始收听' }}
      </button>
    </form>
    <div class="public-footer">
      <span>还没有账号？</span>
      <RouterLink to="/register">提交收听申请</RouterLink>
    </div>
  </PublicFrame>
</template>
