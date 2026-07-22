<script setup lang="ts">
import { computed, type Component } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ConsoleHeader from '../components/ConsoleHeader.vue'
import AdminUsers from '../components/admin/AdminUsers.vue'
import AdminChannels from '../components/admin/AdminChannels.vue'
import AdminTracks from '../components/admin/AdminTracks.vue'
import AdminPlaylist from '../components/admin/AdminPlaylist.vue'

type TabName = 'users' | 'channels' | 'music' | 'playlist'

const route = useRoute()
const router = useRouter()
const tabs: Array<{ id: TabName; label: string; code: string; component: Component }> = [
  { id: 'users', label: '用户审批', code: 'USR', component: AdminUsers },
  { id: 'channels', label: '频道', code: 'CHN', component: AdminChannels },
  { id: 'music', label: '音乐库', code: 'LIB', component: AdminTracks },
  { id: 'playlist', label: '播放列表', code: 'PLS', component: AdminPlaylist },
]

const activeTab = computed<TabName>(() => {
  const requested = route.query.tab
  return tabs.some((tab) => tab.id === requested) ? requested as TabName : 'users'
})
const activeComponent = computed(() => tabs.find((tab) => tab.id === activeTab.value)?.component || AdminUsers)

function selectTab(tab: TabName) {
  void router.replace({ query: { ...route.query, tab } })
}

function handleTabKeydown(event: KeyboardEvent, index: number) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  let next = index
  if (event.key === 'ArrowRight') next = (index + 1) % tabs.length
  if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length
  if (event.key === 'Home') next = 0
  if (event.key === 'End') next = tabs.length - 1
  selectTab(tabs[next].id)
  requestAnimationFrame(() => document.getElementById(`admin-tab-${tabs[next].id}`)?.focus())
}
</script>

<template>
  <div class="console-page admin-page">
    <ConsoleHeader section="ADMIN / MASTER CONTROL" />
    <main id="main-content" class="admin-console">
      <header class="admin-titlebar">
        <div>
          <span class="eyebrow">Restricted operations deck</span>
          <h1>电台总控台</h1>
        </div>
        <div class="admin-titlebar__security">
          <span class="status-lamp status-lamp--active" aria-hidden="true"></span>
          ADMIN SESSION / CSRF ARMED
        </div>
      </header>

      <div class="admin-tabs" role="tablist" aria-label="管理功能">
        <button
          v-for="(tab, index) in tabs"
          :id="`admin-tab-${tab.id}`"
          :key="tab.id"
          type="button"
          role="tab"
          :aria-selected="activeTab === tab.id"
          :aria-controls="`admin-panel-${tab.id}`"
          :tabindex="activeTab === tab.id ? 0 : -1"
          @click="selectTab(tab.id)"
          @keydown="handleTabKeydown($event, index)"
        >
          <span>{{ tab.code }}</span>{{ tab.label }}
        </button>
      </div>

      <section
        :id="`admin-panel-${activeTab}`"
        class="admin-workspace"
        role="tabpanel"
        :aria-labelledby="`admin-tab-${activeTab}`"
        tabindex="0"
      >
        <component :is="activeComponent" />
      </section>
    </main>
  </div>
</template>
