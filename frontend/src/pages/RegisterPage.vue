<script setup lang="ts">
import { reactive, ref } from 'vue'
import { api } from '../api'
import { userFacingError } from '../api/client'
import InlineNotice from '../components/InlineNotice.vue'
import PublicFrame from '../components/PublicFrame.vue'

const form = reactive({ username: '', email: '', password: '', confirmPassword: '' })
const submitting = ref(false)
const error = ref('')
const registered = ref(false)

async function submit() {
  error.value = ''
  if (form.password.length < 10) {
    error.value = '密码至少需要 10 个字符。'
    return
  }
  if (form.password !== form.confirmPassword) {
    error.value = '两次输入的密码不一致。'
    return
  }

  submitting.value = true
  try {
    await api.auth.register({
      username: form.username.trim(),
      email: form.email.trim(),
      password: form.password,
    })
    registered.value = true
  } catch (cause) {
    error.value = userFacingError(cause, '申请提交失败，请检查填写内容')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <PublicFrame eyebrow="Listener request / 02" title="申请收听席位" lead="新账号需要管理员批准。我们不会通过邮件发送验证或密码。">
    <div v-if="registered" class="approval-result" role="status">
      <div class="approval-result__stamp" aria-hidden="true">PENDING</div>
      <span class="eyebrow">Request received</span>
      <h2>申请已进入审批队列</h2>
      <p>管理员批准后，你就可以使用用户名和密码登录。当前没有邮件通知，请稍后直接尝试登录。</p>
      <RouterLink class="button button--primary button--wide" to="/login">返回登录</RouterLink>
    </div>

    <form v-else class="console-form" novalidate @submit.prevent="submit">
      <InlineNotice tone="info">账号资料仅用于电台登录和管理员审批。</InlineNotice>
      <div class="field-grid">
        <div class="field">
          <label for="register-username">用户名</label>
          <input id="register-username" v-model="form.username" autocomplete="username" required autofocus />
        </div>
        <div class="field">
          <label for="register-email">电子邮箱</label>
          <input id="register-email" v-model="form.email" type="email" autocomplete="email" required />
        </div>
      </div>
      <div class="field">
        <label for="register-password">密码</label>
        <input
          id="register-password"
          v-model="form.password"
          type="password"
          autocomplete="new-password"
          minlength="10"
          maxlength="128"
          required
          aria-describedby="password-hint"
        />
        <small id="password-hint">10–128 个字符，建议使用独立的长密码。</small>
      </div>
      <div class="field">
        <label for="register-confirm">确认密码</label>
        <input id="register-confirm" v-model="form.confirmPassword" type="password" autocomplete="new-password" required />
      </div>
      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <button
        class="button button--primary button--wide"
        type="submit"
        :disabled="submitting || !form.username || !form.email || !form.password || !form.confirmPassword"
      >
        {{ submitting ? '正在提交…' : '提交审批申请' }}
      </button>
    </form>
    <div v-if="!registered" class="public-footer">
      <span>已有获批账号？</span>
      <RouterLink to="/login">返回登录</RouterLink>
    </div>
  </PublicFrame>
</template>
