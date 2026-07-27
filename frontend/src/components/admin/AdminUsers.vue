<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../../api'
import { userFacingError } from '../../api/client'
import type { EntityId, ListeningState, User, UserStatus } from '../../api/types'
import { session } from '../../session'
import { formatDateTime } from '../../utils/format'
import InlineNotice from '../InlineNotice.vue'
import StatusBadge from '../StatusBadge.vue'

const users = ref<User[]>([])
const loading = ref(true)
const busyId = ref<EntityId | null>(null)
const error = ref('')
const notice = ref('')
type UserFilter = 'all' | UserStatus | 'online'
const filter = ref<UserFilter>('all')
let refreshTimer: ReturnType<typeof setInterval> | null = null
let loadInFlight = false
let listeningLoadInFlight = false

const filters: Array<{ value: UserFilter; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'online', label: '正在收听' },
  { value: 'pending', label: '待审批' },
  { value: 'approved', label: '已批准' },
  { value: 'disabled', label: '已停用' },
  { value: 'rejected', label: '已拒绝' },
]

function effectiveStatus(user: User): UserStatus {
  if ((user.enabled === false || user.is_active === false) && user.status === 'approved') return 'disabled'
  return user.status
}

function isListening(user: User): boolean {
  return user.listening?.online === true
}

const filteredUsers = computed(() => {
  const source = filter.value === 'all'
    ? users.value
    : filter.value === 'online'
      ? users.value.filter(isListening)
      : users.value.filter((user) => effectiveStatus(user) === filter.value)
  return [...source].sort((a, b) => {
    if (effectiveStatus(a) === 'pending' && effectiveStatus(b) !== 'pending') return -1
    if (effectiveStatus(b) === 'pending' && effectiveStatus(a) !== 'pending') return 1
    return String(b.created_at || '').localeCompare(String(a.created_at || ''))
  })
})

const pendingCount = computed(() => users.value.filter((user) => effectiveStatus(user) === 'pending').length)
const listeningCount = computed(() => users.value.filter(isListening).length)

function offlineListening(): ListeningState {
  return { online: false, channels: [], last_seen_at: null }
}

async function load() {
  if (loadInFlight) return
  loadInFlight = true
  loading.value = true
  error.value = ''
  try {
    users.value = await api.admin.users()
  } catch (cause) {
    error.value = userFacingError(cause, '无法读取用户列表')
  } finally {
    loadInFlight = false
    loading.value = false
  }
}

async function refreshListening() {
  if (listeningLoadInFlight || loadInFlight) return
  listeningLoadInFlight = true
  try {
    const active = await api.admin.listeners()
    const byUserId = new Map(active.map((item) => [String(item.user_id), item]))
    for (const user of users.value) {
      const listening = byUserId.get(String(user.id))
      user.listening = listening
        ? {
            online: listening.online,
            channels: listening.channels,
            last_seen_at: listening.last_seen_at,
          }
        : offlineListening()
    }
  } catch {
    // Keep the last known indicators until the next partial refresh succeeds.
  } finally {
    listeningLoadInFlight = false
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

async function promote(user: User) {
  if (
    busyId.value !== null
    || user.role !== 'listener'
    || effectiveStatus(user) !== 'approved'
  ) return
  if (!window.confirm(
    `确定将 ${user.username} 提升为管理员吗？该用户的现有会话会立即失效，重新登录后将获得全部管理权限。`,
  )) return

  busyId.value = user.id
  error.value = ''
  notice.value = ''
  try {
    await api.admin.promoteUser(user.id)
    notice.value = `已将 ${user.username} 提升为管理员；其现有会话已失效。`
    await load()
  } catch (cause) {
    error.value = userFacingError(cause, '用户提权失败')
  } finally {
    busyId.value = null
  }
}

async function removeUser(user: User) {
  if (
    busyId.value !== null
    || String(user.id) === String(session.state.user?.id)
  ) return
  if (!window.confirm(
    `永久删除用户“${user.username}”？其登录会话和上传任务记录会一并删除，已导入的音频不会删除。此操作不可撤销。`,
  )) return

  busyId.value = user.id
  error.value = ''
  notice.value = ''
  try {
    await api.admin.deleteUser(user.id)
    notice.value = `已永久删除用户 ${user.username}。`
    await load()
  } catch (cause) {
    error.value = userFacingError(cause, '用户删除失败')
  } finally {
    busyId.value = null
  }
}

onMounted(() => {
  void load()
  refreshTimer = setInterval(() => void refreshListening(), 5_000)
})

onBeforeUnmount(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})
</script>

<template>
  <div class="workspace-stack">
    <header class="workspace-header">
      <div>
        <span class="eyebrow">Access control</span>
        <h2>用户与审批</h2>
        <p>批准新听众、授予管理员权限，或停用及永久删除现有账号。</p>
      </div>
      <div class="metric-pair">
        <div>
          <strong>{{ listeningCount }}</strong>
          <span>LISTENING</span>
        </div>
        <div>
          <strong>{{ pendingCount }}</strong>
          <span>PENDING</span>
        </div>
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
          <tr><th>用户</th><th>角色</th><th>账号状态</th><th>收听状态</th><th>申请时间</th><th>最近登录</th><th class="align-right">操作</th></tr>
        </thead>
        <tbody>
          <tr v-if="loading"><td colspan="7" class="table-message">正在读取账号总线…</td></tr>
          <tr v-else-if="!filteredUsers.length"><td colspan="7" class="table-message">当前筛选条件下没有用户。</td></tr>
          <template v-else>
            <tr v-for="user in filteredUsers" :key="user.id">
              <td data-label="用户">
                <strong>{{ user.username }}</strong>
                <small>{{ user.email }}</small>
              </td>
              <td data-label="角色"><span class="mono-label">{{ user.role.toUpperCase() }}</span></td>
              <td data-label="账号状态"><StatusBadge :status="effectiveStatus(user)" /></td>
              <td data-label="收听状态">
                <StatusBadge :status="isListening(user) ? 'online' : 'offline'" />
                <small
                  v-if="isListening(user)"
                  :title="`最近收到音频流请求：${formatDateTime(user.listening?.last_seen_at)}`"
                >
                  {{ user.listening?.channels.map((item) => item.name).join(' / ') }}
                </small>
              </td>
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
                <button
                  v-if="user.role === 'listener' && effectiveStatus(user) === 'approved'"
                  class="button button--positive button--small"
                  type="button"
                  :disabled="busyId !== null"
                  @click="promote(user)"
                >提升为管理员</button>
                <button
                  class="button button--danger button--small"
                  type="button"
                  :disabled="busyId !== null || String(user.id) === String(session.state.user?.id)"
                  :title="String(user.id) === String(session.state.user?.id) ? '不能删除当前登录账号' : '永久删除用户'"
                  @click="removeUser(user)"
                >删除</button>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>
</template>
