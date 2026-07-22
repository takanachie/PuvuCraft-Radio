import { reactive, readonly } from 'vue'
import { api } from './api'
import { ApiError } from './api/client'
import type { User } from './api/types'

interface SessionState {
  user: User | null
  userChecked: boolean
  setupRequired: boolean | null
  setupError: string | null
}

const state = reactive<SessionState>({
  user: null,
  userChecked: false,
  setupRequired: null,
  setupError: null,
})

let setupRequest: Promise<boolean> | null = null
let userRequest: Promise<User | null> | null = null

async function checkSetup(force = false): Promise<boolean> {
  if (setupRequest) return setupRequest
  if (!force && state.setupRequired !== null) return state.setupRequired

  setupRequest = api.setup
    .status()
    .then(({ required }) => {
      state.setupRequired = Boolean(required)
      state.setupError = null
      return state.setupRequired
    })
    .catch((error: unknown) => {
      state.setupError = error instanceof Error ? error.message : '无法读取初始化状态'
      throw error
    })
    .finally(() => {
      setupRequest = null
    })

  return setupRequest
}

async function loadUser(force = false): Promise<User | null> {
  if (userRequest) return userRequest
  if (!force && state.userChecked) return state.user

  userRequest = api.auth
    .me()
    .then((user) => {
      state.user = user
      state.userChecked = true
      return user
    })
    .catch((error: unknown) => {
      if (error instanceof ApiError && error.status === 401) {
        state.user = null
        state.userChecked = true
        return null
      }
      throw error
    })
    .finally(() => {
      userRequest = null
    })

  return userRequest
}

export const session = {
  state: readonly(state),
  checkSetup,
  loadUser,
  setUser(user: User | null) {
    state.user = user
    state.userChecked = true
  },
  clearUser() {
    state.user = null
    state.userChecked = true
  },
  markSetupComplete() {
    state.setupRequired = false
  },
}
