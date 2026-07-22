<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api'
import { userFacingError } from '../../api/client'
import type { EntityId, User, UserStatus } from '../../api/types'
import { session } from '../../session'
import { formatDateTime } from '../../utils/format'
import InlineNotice from '../InlineNotice.vue'
import StatusBadge from '../StatusBadge.vue'

const users = ref<User[]>([])
const loading = ref(true)
const busyId = ref<EntityId | null>(null)
const error = ref('')
const notice = ref('')
const filter = ref<'all' | UserStatus>('all')

const filters: Array<{ value: 'all' | UserStatus; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'pending', label: '待审批' },
  { value: 'approved', label: '已批准' },
  { value: 'disabled', label: '已停用' },
  { value: 'rejected', label: '已拒绝' },
]

function effectiveStatus(user: User): UserStatus {
  if ((user.enabled === false || user.is_active === false) && user.status === 'approved') return 'disabled'
  return user.status
}

const filteredUsers = computed(() => {
  const source = filter.value === 'all'
    ? users.value
    : users.value.filter((user) => effectiveStatus(user) === filter.value)
  return [...source].sort((a, b) => {
    if (effectiveStatus(a) === 'pending' && effectiveStatus(b) !== 'pending') return -1
    if (effectiveStatus(b) === 'pending' && effectiveStatus(a) !== 'pending') return 1
    return String(b.created_at || '').localeCompare(String(a.created_at || ''))
  })
})

const pendingCount = computed(() => users.value.filter((user) => effectiveStatus(user) === 'pending').length)

async function load() {
  loading.value = true
  error.value = ''
  try {
    users.value = await api.admin.users()
  } catch (cause) {
    error.value = userFacingError(cause, '无法读取用户列表')
  } finally {
    loading.value = false
  }
}

async function setStatus(user: User, status: UserStatus) {
  if (busyId.value !== null) return
  const destructive = status === 'rejected' || status === 'disabled'
  if (destructive) {
    const action = status === 'rejected' ? '拒绝此申请' : '停用此账号并使现有会话失效'
    if (!window.confirm(`确定要${action}吗？`)) return
  }

  busyId.value = user.id
  error.value = ''
  notice.value = ''
  try {
    await api.admin.updateUser(user.id, status)
    notice.value = status === 'approved'
      ? `已批准 ${user.username} 的账号。`
      : status === 'disabled'
        ? `已停用 ${user.username}。`
        : `已拒绝 ${user.username} 的申请。`
    await load()
  } catch (cause) {
    error.value = userFacingError(cause, '用户状态更新失败')
  } finally {
    busyId.value = null
  }
}

onMounted(() => void load())
</script>

<template>
  <div class="workspace-stack">
    <header class="workspace-header">
      <div>
        <span class="eyebrow">Access control</span>
        <h2>用户与审批</h2>
        <p>批准新听众，或立即撤销现有账号的会话权限。</p>
      </div>
      <div class="metric-block">
        <strong>{{ pendingCount }}</strong>
        <span>PENDING</span>
      </div>
    </header>

    <InlineNotice v-if="error" tone="danger">{{ error }} <button class="text-button" type="button" @click="load">重试</button></InlineNotice>
    <InlineNotice v-else-if="notice" tone="success">{{ notice }}</InlineNotice>

    <div class="filter-bank" role="group" aria-label="按状态筛选用户">
      <button
        v-for="item in filters"
        :key="item.value"
        type="button"
        :class="{ active: filter === item.value }"
        :aria-pressed="filter === item.value"
        @click="filter = item.value"
      >
        {{ item.label }}
      </button>
      <button class="filter-bank__refresh" type="button" :disabled="loading" @click="load">刷新</button>
    </div>

    <div class="data-frame">
      <table class="console-table user-table">
        <thead>
          <tr><th>用户</th><th>角色</th><th>状态</th><th>申请时间</th><th>最近登录</th><th class="align-right">操作</th></tr>
        </thead>
        <tbody>
          <tr v-if="loading"><td colspan="6" class="table-message">正在读取账号总线…</td></tr>
          <tr v-else-if="!filteredUsers.length"><td colspan="6" class="table-message">当前筛选条件下没有用户。</td></tr>
          <template v-else>
            <tr v-for="user in filteredUsers" :key="user.id">
              <td data-label="用户">
                <strong>{{ user.username }}</strong>
                <small>{{ user.email }}</small>
              </td>
              <td data-label="角色"><span class="mono-label">{{ user.role.toUpperCase() }}</span></td>
              <td data-label="状态"><StatusBadge :status="effectiveStatus(user)" /></td>
              <td data-label="申请时间">{{ formatDateTime(user.created_at) }}</td>
              <td data-label="最近登录">{{ formatDateTime(user.last_login_at) }}</td>
              <td data-label="操作" class="table-actions">
                <template v-if="effectiveStatus(user) === 'pending'">
                  <button class="button button--positive button--small" type="button" :disabled="busyId !== null" @click="setStatus(user, 'approved')">批准</button>
                  <button class="button button--danger button--small" type="button" :disabled="busyId !== null" @click="setStatus(user, 'rejected')">拒绝</button>
                </template>
                <button
                  v-else-if="effectiveStatus(user) === 'approved'"
                  class="button button--danger button--small"
                  type="button"
                  :disabled="busyId !== null || String(user.id) === String(session.state.user?.id)"
                  :title="String(user.id) === String(session.state.user?.id) ? '不能停用当前登录账号' : ''"
                  @click="setStatus(user, 'disabled')"
                >停用</button>
                <button v-else class="button button--positive button--small" type="button" :disabled="busyId !== null" @click="setStatus(user, 'approved')">重新启用</button>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>
</template>
