import { createRouter, createWebHistory } from 'vue-router'
import { session } from './session'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/radio' },
    {
      path: '/setup',
      name: 'setup',
      component: () => import('./pages/SetupPage.vue'),
      meta: { title: '初始化电台' },
    },
    {
      path: '/login',
      name: 'login',
      component: () => import('./pages/LoginPage.vue'),
      meta: { title: '登录', guestOnly: true },
    },
    {
      path: '/register',
      name: 'register',
      component: () => import('./pages/RegisterPage.vue'),
      meta: { title: '申请账号', guestOnly: true },
    },
    {
      path: '/radio',
      name: 'radio',
      component: () => import('./pages/RadioPage.vue'),
      meta: { title: '直播控制台', requiresAuth: true },
    },
    {
      path: '/admin',
      name: 'admin',
      component: () => import('./pages/AdminPage.vue'),
      meta: { title: '管理控制台', requiresAuth: true, requiresAdmin: true },
    },
    { path: '/:pathMatch(.*)*', redirect: '/radio' },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach(async (to) => {
  let setupRequired = false
  try {
    setupRequired = await session.checkSetup(to.name === 'setup')
  } catch {
    // Public pages remain available so they can present a useful connection error.
  }

  if (setupRequired && to.name !== 'setup') return { name: 'setup' }
  if (!setupRequired && to.name === 'setup' && session.state.setupRequired === false) {
    const user = await session.loadUser().catch(() => null)
    return user ? { name: 'radio' } : { name: 'login' }
  }

  if (to.meta.requiresAuth || to.meta.guestOnly) {
    const user = await session.loadUser().catch(() => null)
    if (to.meta.requiresAuth && !user) {
      return { name: 'login', query: { redirect: to.fullPath } }
    }
    if (to.meta.requiresAdmin && user?.role !== 'admin') return { name: 'radio' }
    if (to.meta.guestOnly && user) return { name: 'radio' }
  }

  document.title = `${to.meta.title || '在线电台'} // PuvuCraft Radio`
  return true
})

export default router
