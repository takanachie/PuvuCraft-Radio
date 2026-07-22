<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { api } from '../../api'
import { userFacingError } from '../../api/client'
import type { Channel, ChannelInput, EntityId } from '../../api/types'
import { formatDateTime, slugify } from '../../utils/format'
import InlineNotice from '../InlineNotice.vue'
import StatusBadge from '../StatusBadge.vue'

const channels = ref<Channel[]>([])
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const notice = ref('')
const editingId = ref<EntityId | null>(null)
const creating = ref(false)
const slugTouched = ref(false)

const form = reactive<ChannelInput>({
  name: '',
  slug: '',
  description: '',
  enabled: true,
  playback_mode: 'sequential',
  display_order: 1,
})

const selected = computed(() => channels.value.find((item) => String(item.id) === String(editingId.value)) || null)

function clearMessages() {
  error.value = ''
  notice.value = ''
}

function beginCreate() {
  clearMessages()
  creating.value = true
  editingId.value = null
  slugTouched.value = false
  Object.assign(form, {
    name: '',
    slug: '',
    description: '',
    enabled: true,
    playback_mode: 'sequential',
    display_order: channels.value.length + 1,
  })
}

function edit(channel: Channel) {
  clearMessages()
  creating.value = false
  editingId.value = channel.id
  slugTouched.value = true
  Object.assign(form, {
    name: channel.name,
    slug: channel.slug,
    description: channel.description || '',
    enabled: channel.enabled,
    playback_mode: channel.playback_mode,
    display_order: channel.display_order ?? 1,
  })
}

async function load(preferredId?: EntityId) {
  loading.value = true
  error.value = ''
  try {
    channels.value = (await api.admin.channels()).sort(
      (a, b) => (a.display_order ?? 0) - (b.display_order ?? 0),
    )
    const target = channels.value.find((item) => String(item.id) === String(preferredId ?? editingId.value))
    if (target) edit(target)
    else if (!creating.value && channels.value[0]) edit(channels.value[0])
    else if (!channels.value.length) beginCreate()
  } catch (cause) {
    error.value = userFacingError(cause, '无法读取频道配置')
  } finally {
    loading.value = false
  }
}

async function save() {
  clearMessages()
  if (!form.name.trim() || !form.slug.trim()) {
    error.value = '频道名称和 slug 不能为空。'
    return
  }
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(form.slug)) {
    error.value = 'Slug 只能包含小写字母、数字和单个连字符。'
    return
  }
  if (selected.value?.enabled && !form.enabled && !window.confirm('保存此配置会停止频道并清理旧 HLS 分片。继续吗？')) return

  saving.value = true
  try {
    const payload: ChannelInput = {
      ...form,
      name: form.name.trim(),
      slug: form.slug.trim(),
      description: form.description.trim(),
      display_order: Number(form.display_order),
    }
    const result = creating.value
      ? await api.admin.createChannel(payload)
      : await api.admin.updateChannel(editingId.value as EntityId, payload)
    const message = creating.value ? '频道已创建。' : '频道配置已保存。'
    creating.value = false
    await load(result?.id)
    const saved = channels.value.find((item) => item.slug === payload.slug)
    if (saved) edit(saved)
    notice.value = message
  } catch (cause) {
    error.value = userFacingError(cause, '频道保存失败')
  } finally {
    saving.value = false
  }
}

async function toggleEnabled(channel: Channel) {
  if (channel.enabled && !window.confirm(`停用“${channel.name}”会停止直播并清理旧 HLS 分片。继续吗？`)) return
  clearMessages()
  saving.value = true
  try {
    await api.admin.updateChannel(channel.id, { enabled: !channel.enabled })
    await load(channel.id)
    notice.value = channel.enabled ? '频道已停用。' : '频道已启用。'
  } catch (cause) {
    error.value = userFacingError(cause, '无法切换频道状态')
  } finally {
    saving.value = false
  }
}

async function remove(channel: Channel) {
  if (!window.confirm(`永久删除频道“${channel.name}”及其播放列表配置？此操作不可撤销。`)) return
  clearMessages()
  saving.value = true
  try {
    await api.admin.deleteChannel(channel.id)
    editingId.value = null
    await load()
    notice.value = '频道已删除。'
  } catch (cause) {
    error.value = userFacingError(cause, '频道删除失败')
  } finally {
    saving.value = false
  }
}

watch(() => form.name, (name) => {
  if (creating.value && !slugTouched.value) form.slug = slugify(name)
})

onMounted(() => void load())
</script>

<template>
  <div class="workspace-stack">
    <header class="workspace-header">
      <div>
        <span class="eyebrow">Transmission matrix</span>
        <h2>频道与运行状态</h2>
        <p>管理频道地址、播放模式和 FFmpeg 监督器状态。</p>
      </div>
      <button class="button button--primary" type="button" @click="beginCreate">+ 新建频道</button>
    </header>

    <InlineNotice v-if="error" tone="danger">{{ error }}</InlineNotice>
    <InlineNotice v-else-if="notice" tone="success">{{ notice }}</InlineNotice>

    <div class="split-workspace split-workspace--channels">
      <section class="selector-rail" aria-label="频道列表">
        <div class="selector-rail__head"><span>{{ channels.length }} CHANNELS</span><button class="text-button" type="button" :disabled="loading" @click="load()">刷新</button></div>
        <div v-if="loading" class="rail-message">正在读取频道矩阵…</div>
        <template v-else>
          <button
            v-for="item in channels"
            :key="item.id"
            class="channel-rail-item"
            :class="{ active: !creating && String(editingId) === String(item.id) }"
            type="button"
            @click="edit(item)"
          >
            <span class="channel-rail-item__order">{{ String(item.display_order ?? 0).padStart(2, '0') }}</span>
            <span><strong>{{ item.name }}</strong><small>/{{ item.slug }}</small></span>
            <StatusBadge :status="item.enabled ? (item.status || item.health?.status || 'starting') : 'stopped'" />
          </button>
        </template>
      </section>

      <section class="editor-panel" aria-labelledby="channel-editor-title">
        <header class="editor-panel__head">
          <div><span class="eyebrow">{{ creating ? 'New frequency' : 'Channel parameters' }}</span><h3 id="channel-editor-title">{{ creating ? '新建频道' : form.name }}</h3></div>
          <StatusBadge v-if="selected" :status="selected.enabled ? (selected.status || selected.health?.status) : 'stopped'" />
        </header>

        <form class="console-form compact-form" @submit.prevent="save">
          <div class="field-grid">
            <div class="field">
              <label for="channel-name">频道名称</label>
              <input id="channel-name" v-model="form.name" required />
            </div>
            <div class="field">
              <label for="channel-slug">Slug / HLS 地址</label>
              <input id="channel-slug" v-model="form.slug" required pattern="[a-z0-9]+(?:-[a-z0-9]+)*" spellcheck="false" @input="slugTouched = true" />
            </div>
          </div>
          <div class="field">
            <label for="channel-description">频道说明</label>
            <textarea id="channel-description" v-model="form.description" rows="3"></textarea>
          </div>
          <div class="field-grid field-grid--three">
            <div class="field">
              <label for="channel-mode">播放模式</label>
              <select id="channel-mode" v-model="form.playback_mode">
                <option value="sequential">顺序循环</option>
                <option value="shuffle">随机（轮内不重复）</option>
              </select>
            </div>
            <div class="field">
              <label for="channel-order">显示顺序</label>
              <input id="channel-order" v-model.number="form.display_order" type="number" min="0" step="1" />
            </div>
            <label class="toggle-field">
              <span>运行状态</span>
              <input v-model="form.enabled" type="checkbox" />
              <i aria-hidden="true"></i><strong>{{ form.enabled ? '启用' : '停用' }}</strong>
            </label>
          </div>

          <div class="form-actions">
            <button class="button button--primary" type="submit" :disabled="saving">{{ saving ? '保存中…' : '保存频道配置' }}</button>
            <button v-if="selected" class="button button--quiet" type="button" :disabled="saving" @click="toggleEnabled(selected)">{{ selected.enabled ? '停止频道' : '启动频道' }}</button>
            <button v-if="selected" class="button button--danger push-right" type="button" :disabled="saving" @click="remove(selected)">删除频道</button>
          </div>
        </form>

        <div v-if="selected" class="health-rack">
          <div class="health-rack__cell"><span>FFMPEG</span><strong>{{ selected.health?.ffmpeg_running ? 'RUNNING' : 'STOPPED' }}</strong></div>
          <div class="health-rack__cell"><span>RESTARTS</span><strong>{{ selected.health?.restart_count ?? '—' }}</strong></div>
          <div class="health-rack__cell"><span>LAST START</span><strong>{{ formatDateTime(selected.health?.last_started_at) }}</strong></div>
          <div class="health-rack__error">
            <span>LAST PROCESS ERROR</span>
            <code>{{ selected.health?.last_error || selected.last_error || '没有记录到 FFmpeg 错误' }}</code>
          </div>
          <div v-if="selected.health?.recent_history?.length" class="health-history">
            <span>RECENT PLAYBACK HISTORY</span>
            <ul>
              <li v-for="entry in selected.health?.recent_history?.slice(0, 5) || []" :key="entry.id ?? entry.started_at">
                <strong>{{ entry.track?.title || '未知曲目' }}</strong>
                <small>{{ formatDateTime(entry.started_at) }} / {{ entry.reason || '正常播放' }}</small>
              </li>
            </ul>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>
