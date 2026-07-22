<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { isApiError, userFacingError } from '../api/client'
import InlineNotice from '../components/InlineNotice.vue'
import PublicFrame from '../components/PublicFrame.vue'
import { session } from '../session'

const router = useRouter()
const step = ref(1)
const submitting = ref(false)
const error = ref('')
const form = reactive({
  token: '',
  username: '',
  email: '',
  password: '',
  confirmPassword: '',
})

const steps = [
  { number: 1, label: '验证令牌' },
  { number: 2, label: '管理员' },
  { number: 3, label: '确认' },
]

const canContinue = computed(() => {
  if (step.value === 1) return Boolean(form.token.trim())
  if (step.value === 2) {
    return Boolean(form.username.trim() && form.email.trim() && form.password && form.confirmPassword)
  }
  return true
})

function next() {
  error.value = ''
  if (step.value === 2) {
    if (form.password.length < 10) {
      error.value = '管理员密码至少需要 10 个字符。'
      return
    }
    if (form.password !== form.confirmPassword) {
      error.value = '两次输入的密码不一致。'
      return
    }
  }
  if (canContinue.value && step.value < 3) step.value += 1
}

async function finish() {
  if (submitting.value) return
  error.value = ''
  submitting.value = true
  try {
    await api.setup.create({
      token: form.token.trim(),
      username: form.username.trim(),
      email: form.email.trim(),
      password: form.password,
    })
    session.markSetupComplete()
    const user = await session.loadUser(true).catch(() => null)
    await router.replace(user ? { name: 'radio' } : { name: 'login', query: { initialized: '1' } })
  } catch (cause) {
    if (isApiError(cause) && ['setup_complete', 'setup_race'].includes(cause.code)) {
      session.markSetupComplete()
      await router.replace({ name: 'login', query: { initialized: '1' } })
      return
    }
    error.value = userFacingError(cause, '初始化失败，请核对令牌和账号资料')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <PublicFrame eyebrow="First transmission / Setup" title="建立第一位管理员" lead="此向导只会开放一次。初始化令牌由服务器生成，不会在网页中显示。">
    <ol class="setup-steps" aria-label="初始化进度">
      <li v-for="item in steps" :key="item.number" :class="{ active: step === item.number, complete: step > item.number }">
        <span>{{ step > item.number ? '✓' : item.number }}</span>{{ item.label }}
      </li>
    </ol>

    <form class="console-form setup-form" @submit.prevent="step < 3 ? next() : finish()">
      <section v-if="step === 1" aria-labelledby="setup-token-title">
        <h2 id="setup-token-title">验证一次性令牌</h2>
        <p class="section-copy">在服务器的初始化日志或受保护的令牌文件中找到它。成功创建管理员后，令牌将永久失效。</p>
        <div class="field">
          <label for="setup-token">初始化令牌</label>
          <input
            id="setup-token"
            v-model="form.token"
            type="password"
            autocomplete="off"
            spellcheck="false"
            required
            autofocus
          />
        </div>
        <InlineNotice tone="warning" title="安全提示">不要在聊天、截图或工单中分享此令牌。</InlineNotice>
      </section>

      <section v-else-if="step === 2" aria-labelledby="setup-admin-title">
        <h2 id="setup-admin-title">创建管理员账号</h2>
        <p class="section-copy">该账号可审批用户、管理媒体库并控制所有频道的服务器播放。</p>
        <div class="field-grid">
          <div class="field">
            <label for="setup-username">管理员用户名</label>
            <input id="setup-username" v-model="form.username" autocomplete="username" required autofocus />
          </div>
          <div class="field">
            <label for="setup-email">电子邮箱</label>
            <input id="setup-email" v-model="form.email" type="email" autocomplete="email" required />
          </div>
        </div>
        <div class="field">
          <label for="setup-password">密码</label>
          <input id="setup-password" v-model="form.password" type="password" autocomplete="new-password" minlength="10" maxlength="128" required />
        </div>
        <div class="field">
          <label for="setup-confirm">确认密码</label>
          <input id="setup-confirm" v-model="form.confirmPassword" type="password" autocomplete="new-password" required />
        </div>
      </section>

      <section v-else aria-labelledby="setup-review-title">
        <h2 id="setup-review-title">最后确认</h2>
        <p class="section-copy">提交后初始化入口关闭，管理员账号立即生效。</p>
        <dl class="review-list">
          <div><dt>初始化令牌</dt><dd>已输入，不显示内容</dd></div>
          <div><dt>管理员</dt><dd>{{ form.username }}</dd></div>
          <div><dt>邮箱</dt><dd>{{ form.email }}</dd></div>
          <div><dt>权限</dt><dd>全部频道与系统管理权限</dd></div>
        </dl>
      </section>

      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <div class="form-actions form-actions--spread">
        <button v-if="step > 1" class="button button--quiet" type="button" :disabled="submitting" @click="step -= 1">上一步</button>
        <span v-else></span>
        <button class="button button--primary" type="submit" :disabled="submitting || !canContinue">
          {{ submitting ? '正在建立电台…' : step === 3 ? '创建管理员并启用电台' : '继续' }}
        </button>
      </div>
    </form>
  </PublicFrame>
</template>
